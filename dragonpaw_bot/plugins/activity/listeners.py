"""Activity plugin: event listeners for message, reaction, and voice tracking."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.models import (
    ContributionBucket,
    ContributionKind,
    UserActivity,
)
from dragonpaw_bot.utils import guild_member, message_has_media

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

loader = lightbulb.Loader()

# guild_id → {user_id → join_timestamp}
_vc_sessions: dict[int, dict[int, float]] = {}


def _channel_multiplier(
    meta: activity_state.ActivityGuildMeta, channel_id: int
) -> float:
    """The channel's configured point multiplier (1.0 when unconfigured, 0 = ignore)."""
    channel_cfg = next(
        (c for c in meta.config.channel_configs if c.channel_id == channel_id),
        None,
    )
    return channel_cfg.point_multiplier if channel_cfg else 1.0


def _add_contribution(
    guild_id: int,
    user_id: int,
    kind: ContributionKind,
    amount: float,
    now: float | None = None,
) -> None:
    """Upsert a contribution into the user's hourly bucket."""
    if now is None:
        now = time.time()
    hour = int(now) // 3600 * 3600

    ua = activity_state.load_user(guild_id, user_id)
    if ua is None:
        ua = UserActivity(user_id=user_id)
        activity_state._user_cache[(guild_id, user_id)] = ua

    for b in ua.buckets:
        if b.hour == hour and b.kind == kind:
            b.amount += amount
            activity_state.mark_user_dirty(guild_id, user_id)
            logger.debug(
                "Activity recorded", user_id=user_id, kind=kind.value, raw_points=amount
            )
            return

    ua.buckets.append(ContributionBucket(hour=hour, kind=kind, amount=amount))
    activity_state.mark_user_dirty(guild_id, user_id)
    logger.debug(
        "Activity recorded", user_id=user_id, kind=kind.value, raw_points=amount
    )


def _ensure_guild_name(
    meta: activity_state.ActivityGuildMeta, bot: DragonpawBot, guild_id: int
) -> None:
    """Populate guild_name on meta if it's missing (best-effort from cache)."""
    if not meta.guild_name:
        guild = bot.cache.get_guild(guild_id)
        if guild:
            meta.guild_name = guild.name
            try:
                activity_state.save_config(meta)
            except Exception:
                logger.warning("Failed to persist guild name", guild_id=guild_id)


@loader.listener(hikari.GuildMessageCreateEvent)
async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
    """Track text and media post contributions."""
    try:
        await _handle_message(event)
    except Exception:
        logger.exception("Error in activity on_message", guild_id=int(event.guild_id))


async def _handle_message(event: hikari.GuildMessageCreateEvent) -> None:
    if event.message.author.is_bot:
        return

    bot: DragonpawBot = event.app  # type: ignore[assignment]
    guild_id = int(event.guild_id)
    meta = activity_state.load_config(guild_id)
    _ensure_guild_name(meta, bot, guild_id)

    try:
        member = await guild_member(bot, event.guild_id, event.author_id)
    except hikari.HTTPError:
        logger.warning(
            "Failed to fetch member for activity tracking",
            guild=meta.guild_name,
            user_id=int(event.author_id),
        )
        return
    if member is None:
        return

    role_ids = [int(r) for r in member.role_ids]
    if not role_ids:
        return  # Not yet through onboarding

    kind = (
        ContributionKind.MEDIA
        if message_has_media(event.message)
        else ContributionKind.TEXT
    )

    amount = _channel_multiplier(meta, int(event.channel_id))
    if amount == 0:
        return

    _add_contribution(guild_id, int(event.author_id), kind, amount)


@loader.listener(hikari.GuildReactionAddEvent)
async def on_reaction(event: hikari.GuildReactionAddEvent) -> None:
    """Track reaction contributions."""
    try:
        await _handle_reaction(event)
    except Exception:
        logger.exception("Error in activity on_reaction", guild_id=int(event.guild_id))


async def _handle_reaction(event: hikari.GuildReactionAddEvent) -> None:
    bot: DragonpawBot = event.app  # type: ignore[assignment]
    guild_id = int(event.guild_id)
    meta = activity_state.load_config(guild_id)
    _ensure_guild_name(meta, bot, guild_id)

    try:
        member = await guild_member(bot, event.guild_id, event.user_id)
    except hikari.HTTPError:
        logger.warning(
            "Failed to fetch member for activity tracking",
            guild=meta.guild_name,
            user_id=int(event.user_id),
        )
        return
    if member is None:
        return

    if member.is_bot:
        return

    role_ids = [int(r) for r in member.role_ids]
    if not role_ids:
        return

    amount = _channel_multiplier(meta, int(event.channel_id))
    if amount == 0:
        return

    _add_contribution(guild_id, int(event.user_id), ContributionKind.REACTION, amount)


@loader.listener(hikari.VoiceStateUpdateEvent)
async def on_voice_state_update(event: hikari.VoiceStateUpdateEvent) -> None:
    """Track voice channel time contributions."""
    if event.guild_id is None:
        return
    try:
        await _handle_voice_state_update(event)
    except Exception:
        logger.exception(
            "Error in activity on_voice_state_update", guild_id=int(event.guild_id)
        )


async def _handle_voice_state_update(event: hikari.VoiceStateUpdateEvent) -> None:
    bot: DragonpawBot = event.app  # type: ignore[assignment]
    guild_id = int(event.guild_id)
    user_id = int(event.state.user_id)

    old_channel = event.old_state.channel_id if event.old_state else None
    new_channel = event.state.channel_id

    # Leave (or switch away from old channel): record accumulated time
    if old_channel is not None:
        sessions = _vc_sessions.get(guild_id, {})
        join_time = sessions.pop(user_id, None)
        if join_time is not None:
            minutes = (time.time() - join_time) / 60.0
            if minutes >= 1.0:
                try:
                    member = await guild_member(
                        bot, event.guild_id, event.state.user_id
                    )
                except hikari.HTTPError:
                    logger.warning(
                        "Failed to fetch member for VC activity",
                        guild_id=guild_id,
                        user_id=user_id,
                    )
                    member = None

                if member and not member.is_bot:
                    meta = activity_state.load_config(guild_id)
                    _ensure_guild_name(meta, bot, guild_id)
                    role_ids = [int(r) for r in member.role_ids]
                    channel_mult = _channel_multiplier(meta, int(old_channel))
                    if role_ids and channel_mult != 0:
                        _add_contribution(
                            guild_id,
                            user_id,
                            ContributionKind.VC,
                            minutes * channel_mult,
                        )

    # Join (or switch to new channel): start tracking
    if new_channel is not None:
        _vc_sessions.setdefault(guild_id, {})[user_id] = time.time()
