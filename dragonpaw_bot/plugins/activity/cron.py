"""Cron tasks for the activity tracker plugin."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, cast

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.models import (
    ACTIVITY_FLOOR,
    BASE_HALF_LIFE,
    PRUNE_DAYS_MAX,
    ActivityGuildMeta,
    best_role_config,
    bucket_is_negligible,
    calculate_score,
    has_ignored_role,
)

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)
loader = lightbulb.Loader()


@loader.task(lightbulb.crontrigger("20 * * * *"))
async def activity_flush(bot: hikari.GatewayBot) -> None:
    """Hourly task: flush dirty in-memory user state to disk."""
    flushed = activity_state.flush_dirty()
    if flushed:
        logger.debug("Activity state flushed", users_written=flushed)


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
    meta = activity_state.load_config(int(guild.id))
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

    bucket_count = _prune_state(meta, members, now)

    if meta.config.lurker_role_id:
        if bucket_count < 7 * 24:
            logger.debug(
                "Skipping lurker sync — not enough history yet",
                guild=meta.guild_name,
                bucket_count=bucket_count,
            )
        else:
            await _sync_lurker_role(gc, meta, members, now, int(guild.owner_id))


def _prune_state(
    meta: ActivityGuildMeta,
    members: dict[int, hikari.Member],
    now: float,
) -> int:
    """Prune old buckets and departed users. Returns total remaining bucket count."""
    cutoff = now - PRUNE_DAYS_MAX * 24 * 3600
    removed_users = 0
    changed = False
    total_buckets = 0

    for user_id in activity_state.list_user_ids(meta.guild_id):
        try:
            if user_id not in members:
                activity_state.delete_user(meta.guild_id, user_id)
                removed_users += 1
                changed = True
                continue

            member = members[user_id]
            role_ids = [int(r) for r in member.role_ids]
            rc = best_role_config(role_ids, meta.config.role_configs)
            half_life = BASE_HALF_LIFE * (rc.decay_multiplier if rc else 1.0)
            cm = rc.contribution_multiplier if rc else 1.0

            ua = activity_state.load_user(meta.guild_id, user_id)
            if ua is None:
                activity_state.delete_user(meta.guild_id, user_id)
                changed = True
                continue

            original_count = len(ua.buckets)
            ua.buckets = [
                b
                for b in ua.buckets
                if b.hour >= cutoff and not bucket_is_negligible(b, now, half_life, cm)
            ]

            if not ua.buckets:
                activity_state.delete_user(meta.guild_id, user_id)
                removed_users += 1
                changed = True
            else:
                total_buckets += len(ua.buckets)
                if len(ua.buckets) < original_count:
                    activity_state.save_user(meta.guild_id, user_id, ua)
                    changed = True
        except Exception:
            logger.exception(
                "Error pruning user activity — skipping",
                guild=meta.guild_name,
                user_id=user_id,
            )

    if changed:
        logger.debug(
            "Activity pruned",
            guild=meta.guild_name,
            removed_users=removed_users,
        )
    return total_buckets


async def _sync_lurker_role(
    gc: GuildContext,
    meta: ActivityGuildMeta,
    members: dict[int, hikari.Member],
    now: float,
    owner_id: int,
) -> None:
    lurker_role_id = meta.config.lurker_role_id
    assert lurker_role_id

    added: list[str] = []
    removed: list[str] = []

    for member in members.values():
        if member.is_bot:
            continue
        if int(member.id) == owner_id:
            continue
        role_ids = [int(r) for r in member.role_ids]
        if not role_ids or has_ignored_role(role_ids, meta.config.role_configs):
            continue

        ua = activity_state.load_user(meta.guild_id, int(member.id))
        buckets = ua.buckets if ua is not None else []
        score = calculate_score(
            buckets, best_role_config(role_ids, meta.config.role_configs), now=now
        )
        should_be_lurker = score < ACTIVITY_FLOOR
        has_lurker = lurker_role_id in role_ids

        if (
            should_be_lurker
            and not has_lurker
            and await _set_lurker(gc, member, lurker_role_id, add=True)
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
            and await _set_lurker(gc, member, lurker_role_id, add=False)
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
            await gc.bot.rest.add_role_to_member(gc.guild_id, member.id, lurker_role_id)
        else:
            await gc.bot.rest.remove_role_from_member(
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
