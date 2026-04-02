"""Activity plugin: event listeners for message, reaction, and voice tracking."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.models import (
    ContributionBucket,
    UserActivity,
    has_ignored_role,
)

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

loader = lightbulb.Loader()

_URL_RE = re.compile(r"https?://", re.IGNORECASE)

# guild_id → {user_id → join_timestamp}
_vc_sessions: dict[int, dict[int, float]] = {}

# Guilds with unsaved in-memory state changes
_dirty_guilds: set[int] = set()


def _has_media(message: hikari.Message) -> bool:
    return (
        bool(message.attachments)
        or _URL_RE.search(message.content or "") is not None
        or bool(message.stickers)
    )


def _add_contribution(
    state: activity_state.ActivityGuildState,
    user_id: int,
    kind: str,
    amount: float,
    now: float | None = None,
) -> None:
    """Upsert a contribution into the user's hourly bucket."""
    if now is None:
        now = time.time()
    hour = int(now) // 3600 * 3600
    if user_id not in state.users:
        state.users[user_id] = UserActivity(user_id=user_id)
    buckets = state.users[user_id].buckets
    for b in buckets:
        if b.hour == hour and b.kind == kind:
            b.amount += amount
            return
    buckets.append(ContributionBucket(hour=hour, kind=kind, amount=amount))


def _ensure_guild_name(
    st: activity_state.ActivityGuildState, bot: DragonpawBot, guild_id: int
) -> None:
    """Populate guild_name on state if it's missing (best-effort from cache)."""
    if not st.guild_name:
        guild = bot.cache.get_guild(guild_id)
        if guild:
            st.guild_name = guild.name


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
    st = activity_state.load(guild_id)
    _ensure_guild_name(st, bot, guild_id)

    member = bot.cache.get_member(event.guild_id, event.author_id)
    if member is None:
        try:
            member = await bot.rest.fetch_member(event.guild_id, event.author_id)
        except hikari.NotFoundError:
            return
        except hikari.HTTPError:
            logger.warning(
                "Failed to fetch member for activity tracking",
                guild=st.guild_name,
                user_id=int(event.author_id),
            )
            return

    role_ids = [int(r) for r in member.role_ids]
    if not role_ids:
        return  # Not yet through onboarding

    if has_ignored_role(role_ids, st.config.role_configs):
        return

    kind = "media" if _has_media(event.message) else "text"

    # Per-channel point multiplier
    channel_cfg = next(
        (c for c in st.config.channel_configs if c.channel_id == int(event.channel_id)),
        None,
    )
    amount = channel_cfg.point_multiplier if channel_cfg else 1.0

    _add_contribution(st, int(event.author_id), kind, amount)
    _dirty_guilds.add(guild_id)


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
    st = activity_state.load(guild_id)
    _ensure_guild_name(st, bot, guild_id)

    member = bot.cache.get_member(event.guild_id, event.user_id)
    if member is None:
        try:
            member = await bot.rest.fetch_member(event.guild_id, event.user_id)
        except hikari.NotFoundError:
            return
        except hikari.HTTPError:
            logger.warning(
                "Failed to fetch member for activity tracking",
                guild=st.guild_name,
                user_id=int(event.user_id),
            )
            return

    if member.is_bot:
        return

    role_ids = [int(r) for r in member.role_ids]
    if not role_ids:
        return

    if has_ignored_role(role_ids, st.config.role_configs):
        return

    _add_contribution(st, int(event.user_id), "reaction", 1.0)
    _dirty_guilds.add(guild_id)


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
                member = bot.cache.get_member(event.guild_id, event.state.user_id)
                if member is None:
                    try:
                        member = await bot.rest.fetch_member(
                            event.guild_id, event.state.user_id
                        )
                    except hikari.NotFoundError:
                        member = None
                    except hikari.HTTPError:
                        logger.warning(
                            "Failed to fetch member for VC activity",
                            guild_id=guild_id,
                            user_id=user_id,
                        )
                        member = None

                if member and not member.is_bot:
                    st = activity_state.load(guild_id)
                    _ensure_guild_name(st, bot, guild_id)
                    role_ids = [int(r) for r in member.role_ids]
                    if role_ids and not has_ignored_role(
                        role_ids, st.config.role_configs
                    ):
                        _add_contribution(st, user_id, "vc", minutes)
                        _dirty_guilds.add(guild_id)

    # Join (or switch to new channel): start tracking
    if new_channel is not None:
        _vc_sessions.setdefault(guild_id, {})[user_id] = time.time()
