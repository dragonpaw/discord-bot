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
from dragonpaw_bot.plugins.intros import state as intros_state

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot
    from dragonpaw_bot.plugins.intros.models import IntrosGuildState

logger = structlog.get_logger(__name__)
loader = lightbulb.Loader()


@loader.listener(hikari.StoppingEvent)
async def on_stopping(_: hikari.StoppingEvent) -> None:
    """Flush any unsaved activity state to disk on shutdown."""
    flushed = activity_state.flush_dirty()
    if flushed:
        logger.info("Activity state flushed on shutdown", users_written=flushed)


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


async def _load_intros_data(
    gc: GuildContext,
) -> tuple[IntrosGuildState, set[int]] | None:
    """Return (intros_state, posted_user_ids) if intros is configured & readable, else None.

    None means the no-introduction lurker check is skipped — either intros isn't
    configured, or the channel can't be read (permissions / HTTP error).
    """
    intros_st = intros_state.load(gc.guild_id)
    if intros_st.channel_id is None:
        return None
    posted: set[int] = set()
    try:
        async for message in gc.bot.rest.fetch_messages(intros_st.channel_id):
            if not message.author.is_bot and not message.is_pinned:
                posted.add(int(message.author.id))
    except hikari.ForbiddenError:
        logger.warning(
            "Cannot read intros channel for lurker sync",
            guild=gc.name,
            channel=intros_st.channel_name,
        )
        await gc.log(
            f"⚠️ I couldn't peek inside <#{intros_st.channel_id}> for the "
            "no-introduction lurker check — I need **Read Message History**! 🐾"
        )
        return None
    except hikari.HTTPError:
        logger.warning(
            "HTTP error fetching intros for lurker sync",
            guild=gc.name,
            channel=intros_st.channel_name,
        )
        return None
    return intros_st, posted


def _evaluate_lurker(
    member: hikari.Member,
    role_ids: list[int],
    score: float,
    meta: ActivityGuildMeta,
    intros: tuple[IntrosGuildState, set[int]] | None,
) -> tuple[bool, str]:
    """Decide whether the member should hold the lurker role.

    The reason is the state-transition label used when the role is actually
    added or removed (members already in the correct state are filtered out
    by the caller, so the label is only surfaced for true transitions):
      should_be_lurker=True  → 'no longer active' | 'no introduction'
      should_be_lurker=False → 'gained immunity' | 'now active'
    """
    if has_ignored_role(role_ids, meta.config.role_configs):
        return False, "gained immunity"
    if score < ACTIVITY_FLOOR:
        return True, "no longer active"
    if intros is not None:
        intros_st, posted_ids = intros
        applies = (
            intros_st.required_role_id is None or intros_st.required_role_id in role_ids
        )
        if applies and int(member.id) not in posted_ids:
            return True, "no introduction"
    return False, "now active"


async def _sync_lurker_role(  # noqa: PLR0912
    gc: GuildContext,
    meta: ActivityGuildMeta,
    members: dict[int, hikari.Member],
    now: float,
    owner_id: int,
) -> None:
    lurker_role_id = meta.config.lurker_role_id
    assert lurker_role_id

    intros = await _load_intros_data(gc)

    added_by_reason: dict[str, list[str]] = {}
    removed_by_reason: dict[str, list[str]] = {}

    for member in members.values():
        if member.is_bot:
            continue
        if int(member.id) == owner_id:
            continue
        role_ids = [int(r) for r in member.role_ids]
        if not role_ids:
            continue

        ua = activity_state.load_user(meta.guild_id, int(member.id))
        if ua is None:
            buckets = []
        else:
            buckets = ua.buckets
        score = calculate_score(
            buckets, best_role_config(role_ids, meta.config.role_configs), now=now
        )
        should_be_lurker, reason = _evaluate_lurker(
            member, role_ids, score, meta, intros
        )
        has_lurker = lurker_role_id in role_ids

        if should_be_lurker == has_lurker:
            continue

        if should_be_lurker:
            if not await _set_lurker(gc, member, lurker_role_id, add=True):
                continue
            added_by_reason.setdefault(reason, []).append(member.mention)
            logger.info(
                "Lurker role added",
                guild=gc.name,
                user=member.display_name,
                reason=reason,
                score=score,
            )
        else:
            if not await _set_lurker(gc, member, lurker_role_id, add=False):
                continue
            removed_by_reason.setdefault(reason, []).append(member.mention)
            logger.info(
                "Lurker role removed",
                guild=gc.name,
                user=member.display_name,
                reason=reason,
                score=score,
            )

    if not (added_by_reason or removed_by_reason):
        return

    lines = ["💤 Lurker role shuffle —"]
    for r, names in added_by_reason.items():
        lines.append(f"• Added ({r}) **{len(names)}**: {', '.join(names)}")
    for r, names in removed_by_reason.items():
        lines.append(f"• Removed ({r}) **{len(names)}**: {', '.join(names)}")
    lines.append("🐉")
    await gc.log("\n".join(lines))


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
