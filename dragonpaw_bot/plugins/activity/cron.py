"""Cron tasks for the activity tracker plugin."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, cast

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.activity import _dirty_guilds, loader
from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.models import (
    ACTIVITY_FLOOR,
    PRUNE_DAYS,
    ActivityGuildState,
    best_role_config,
    calculate_score,
    has_ignored_role,
)

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)


@loader.task(lightbulb.crontrigger("20 * * * *"))
async def activity_flush(bot: hikari.GatewayBot) -> None:
    """Hourly task: flush dirty in-memory state to disk."""
    bot = cast("DragonpawBot", bot)
    for guild_id in list(_dirty_guilds):
        st = activity_state._cache.get(guild_id)
        if st is not None:
            activity_state.save(st)
            logger.debug("Activity state flushed", guild=st.guild_name)
        _dirty_guilds.discard(guild_id)


@loader.task(lightbulb.crontrigger("15 4 * * *"))
async def activity_daily_cron(bot: hikari.GatewayBot) -> None:
    """Daily task: prune old buckets, remove departed users, sync lurker role."""
    bot = cast("DragonpawBot", bot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.debug("Activity daily cron run", guild_count=len(guilds))

    for guild in guilds:
        try:
            await _daily_guild(bot, guild)
        except Exception:
            logger.exception("Error during activity daily cron", guild=guild.name)


async def _daily_guild(bot: DragonpawBot, guild: hikari.Guild) -> None:
    gc = GuildContext.from_guild(bot, guild)
    st = activity_state.load(int(guild.id))
    now = time.time()

    # Fetch members once for both prune and lurker sync
    members: dict[int, hikari.Member] = {}
    try:
        async for member in bot.rest.fetch_members(guild.id):
            members[int(member.id)] = member
    except hikari.HTTPError:
        logger.warning("Failed to fetch members for activity cron", guild=guild.name)
        await gc.log(
            "⚠️ Couldn't fetch the member list for today's activity update — will try again tomorrow! 🐾"
        )
        return

    _prune_state(st, set(members.keys()), now)

    if st.config.lurker_role_id:
        await _sync_lurker_role(bot, gc, st, members, now)


def _prune_state(st: ActivityGuildState, member_ids: set[int], now: float) -> None:
    cutoff = now - PRUNE_DAYS * 24 * 3600
    to_delete: list[int] = []
    changed = False

    for user_id, user_activity in list(st.users.items()):
        original_count = len(user_activity.buckets)
        user_activity.buckets = [b for b in user_activity.buckets if b.hour >= cutoff]
        if user_id not in member_ids or not user_activity.buckets:
            to_delete.append(user_id)
            changed = True
        elif len(user_activity.buckets) < original_count:
            changed = True

    for user_id in to_delete:
        del st.users[user_id]

    if changed:
        activity_state.save(st)
        logger.debug(
            "Activity pruned",
            guild=st.guild_name,
            removed_users=len(to_delete),
        )


async def _sync_lurker_role(
    bot: DragonpawBot,
    gc: GuildContext,
    st: ActivityGuildState,
    members: dict[int, hikari.Member],
    now: float,
) -> None:
    lurker_role_id = st.config.lurker_role_id
    assert lurker_role_id

    added: list[str] = []
    removed: list[str] = []

    for member in members.values():
        if member.is_bot:
            continue
        role_ids = [int(r) for r in member.role_ids]
        if not role_ids or has_ignored_role(role_ids, st.config.role_configs):
            continue

        user_activity = st.users.get(int(member.id))
        buckets = user_activity.buckets if user_activity else []
        score = calculate_score(
            buckets, best_role_config(role_ids, st.config.role_configs), now=now
        )
        should_be_lurker = score < ACTIVITY_FLOOR
        has_lurker = lurker_role_id in role_ids

        if (
            should_be_lurker
            and not has_lurker
            and await _set_lurker(bot, gc, member, lurker_role_id, add=True)
        ):
            added.append(member.display_name)
            logger.info(
                "Lurker role added",
                guild=gc.name,
                user=member.display_name,
                score=score,
            )
        elif (
            not should_be_lurker
            and has_lurker
            and await _set_lurker(bot, gc, member, lurker_role_id, add=False)
        ):
            removed.append(member.display_name)
            logger.info(
                "Lurker role removed",
                guild=gc.name,
                user=member.display_name,
                score=score,
            )

    if added or removed:
        parts: list[str] = []
        if added:
            parts.append(f"added to **{len(added)}**: {', '.join(added)}")
        if removed:
            parts.append(f"removed from **{len(removed)}**: {', '.join(removed)}")
        await gc.log(f"💤 Lurker role sync — {'; '.join(parts)} 🐉")


async def _set_lurker(
    bot: DragonpawBot,
    gc: GuildContext,
    member: hikari.Member,
    lurker_role_id: int,
    *,
    add: bool,
) -> bool:
    """Add or remove the lurker role. Returns True on success, False on failure."""
    action = "assign" if add else "remove"
    preposition = "to" if add else "from"
    try:
        if add:
            await bot.rest.add_role_to_member(gc.guild_id, member.id, lurker_role_id)
        else:
            await bot.rest.remove_role_from_member(
                gc.guild_id, member.id, lurker_role_id
            )
    except hikari.NotFoundError:
        return False  # Member left between fetch and assignment
    except hikari.ForbiddenError:
        logger.warning(
            f"Cannot {action} lurker role", guild=gc.name, user=member.display_name
        )
        await gc.log(
            f"⚠️ I can't {action} the lurker role {preposition} {member.mention} — please check my role hierarchy! 🐉"
        )
        return False
    except hikari.HTTPError:
        logger.warning(
            f"HTTP error on lurker role {action}",
            guild=gc.name,
            user=member.display_name,
        )
        return False
    return True
