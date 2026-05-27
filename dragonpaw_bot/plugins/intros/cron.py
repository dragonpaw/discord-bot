from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import (
    CHANNEL_CLEANUP_PERMS,
    GuildContext,
    check_channel_perms,
)
from dragonpaw_bot.plugins.intros import state as intros_state
from dragonpaw_bot.utils import guild_member, guild_members

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot
    from dragonpaw_bot.plugins.intros.models import IntrosGuildState

logger = structlog.get_logger(__name__)
loader = lightbulb.Loader()


@dataclass
class IntrosScanResult:
    missing: list[hikari.Member] = field(default_factory=list)
    role_added: list[hikari.Member] = field(default_factory=list)
    role_removed: list[hikari.Member] = field(default_factory=list)
    role_failed: bool = False


async def scan_intros(
    gc: GuildContext,
    st: IntrosGuildState,
    *,
    members: list[hikari.Member] | None = None,
    posted_ids: set[int] | None = None,
) -> IntrosScanResult:
    """Find members missing intros and, if configured, reconcile the role.

    The message listener removes the role live when a flagged member posts; this
    reconciliation is the safety net for removals that never fired (e.g. the bot
    was offline when they posted). It only touches members who pass the
    `required_role_id` filter — non-eligible holders (e.g. a validation-seeded
    member who doesn't yet have the required role) keep the role until they post.
    Pass `members` / `posted_ids` to reuse data the caller already fetched. Caller
    must ensure `st.channel_id` is set.
    """
    assert st.channel_id is not None

    if posted_ids is None:
        posted_ids = set()
        async for message in gc.bot.rest.fetch_messages(st.channel_id):
            if not message.author.is_bot and not message.is_pinned:
                posted_ids.add(int(message.author.id))

    if members is None:
        members = await guild_members(gc.bot, gc.guild_id)

    result, role_holders = _classify_members(st, members, posted_ids)

    if st.missing_role_id is not None:
        await _sync_missing_role(gc, st, result, role_holders)

    return result


def _classify_members(
    st: IntrosGuildState, members: list[hikari.Member], posted_ids: set[int]
) -> tuple[IntrosScanResult, list[hikari.Member]]:
    """Split eligible members into those missing an intro and current role-holders.

    Only members passing the `required_role_id` filter are considered; non-eligible
    members are never flagged missing nor stripped of the role.
    """
    result = IntrosScanResult()
    role_holders: list[hikari.Member] = []
    role_id = st.missing_role_id
    for member in members:
        if member.is_bot:
            continue
        member_role_ids = [int(r) for r in member.role_ids]
        if (
            st.required_role_id is not None
            and st.required_role_id not in member_role_ids
        ):
            continue
        if role_id is not None and role_id in member_role_ids:
            role_holders.append(member)
        if int(member.id) not in posted_ids:
            result.missing.append(member)
    return result, role_holders


async def _sync_missing_role(
    gc: GuildContext,
    st: IntrosGuildState,
    result: IntrosScanResult,
    role_holders: list[hikari.Member],
) -> None:
    """Reconcile the missing-intro role: missing members get it, everyone else loses it."""
    missing_ids = {int(m.id) for m in result.missing}
    holder_ids = {int(m.id) for m in role_holders}

    for member in result.missing:
        if int(member.id) in holder_ids:
            continue
        if await _set_role(gc, st, result, member, add=True):
            result.role_added.append(member)
        if result.role_failed:
            return

    for member in role_holders:
        if int(member.id) in missing_ids:
            continue
        if await _set_role(gc, st, result, member, add=False):
            result.role_removed.append(member)
        if result.role_failed:
            return


async def _set_role(
    gc: GuildContext,
    st: IntrosGuildState,
    result: IntrosScanResult,
    member: hikari.Member,
    *,
    add: bool,
) -> bool:
    """Add or remove the missing-intro role for one member. Returns True if changed.

    Sets `result.role_failed` on a permission error so the caller can bail — a
    hierarchy problem will block every subsequent change too.
    """
    assert st.missing_role_id is not None
    role_id = st.missing_role_id
    verb = "add" if add else "remove"
    try:
        if add:
            await gc.bot.rest.add_role_to_member(gc.guild_id, member.id, role_id)
        else:
            await gc.bot.rest.remove_role_from_member(gc.guild_id, member.id, role_id)
    except hikari.NotFoundError:
        return False
    except hikari.ForbiddenError:
        logger.warning(
            f"Cannot {verb} missing-intro role",
            guild=gc.name,
            user=member.display_name,
            role=st.missing_role_name,
        )
        result.role_failed = True
        return False
    except hikari.HTTPError:
        logger.warning(
            f"HTTP error during {verb} of missing-intro role",
            guild=gc.name,
            user=member.display_name,
        )
        return False
    else:
        logger.info(
            f"{'Added' if add else 'Removed'} missing-intro role",
            guild=gc.name,
            user=member.display_name,
            role=st.missing_role_name,
        )
        return True


async def _try_delete(message: hikari.Message, reason: str, guild_name: str) -> bool:
    """Delete a message, logging on permission errors. Returns True if deleted."""
    try:
        await message.delete()
    except hikari.ForbiddenError:
        logger.warning(
            f"Cannot delete {reason} — missing permissions",
            guild=guild_name,
            user=message.author.username,
        )
        return False
    except hikari.NotFoundError:
        return False
    else:
        return True


@loader.task(lightbulb.crontrigger("30 9 * * *"))
async def intros_daily(bot: hikari.GatewayBot) -> None:
    """Daily task: tidy stale posts, then reconcile the missing-intro role."""
    bot = cast("DragonpawBot", bot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.debug("Intros daily run", guild_count=len(guilds))

    for guild in guilds:
        try:
            await _daily_guild(bot, guild)
        except Exception:
            logger.exception("Error during intros daily run", guild=guild.name)


async def _daily_guild(bot: DragonpawBot, guild: hikari.Guild) -> None:
    """Fetch the channel once, then clean up stale posts and reconcile the role."""
    gc = GuildContext.from_guild(bot, guild)
    st = intros_state.load(int(guild.id))

    if st.channel_id is None:
        return

    logger.debug("Checking intros channel", guild=guild.name, channel=st.channel_name)

    missing_perms = await check_channel_perms(
        bot, guild.id, hikari.Snowflake(st.channel_id), CHANNEL_CLEANUP_PERMS
    )
    if missing_perms:
        logger.warning(
            "Missing permissions in intros channel",
            guild=guild.name,
            channel=st.channel_name,
            missing=missing_perms,
        )
        await gc.log(
            f"⚠️ *sniffs around the intros channel* I'm missing **{', '.join(missing_perms)}** "
            f"in <#{st.channel_id}> and can't tidy up old posts from members who've left. "
            f"Could someone fix my permissions? 🐉"
        )
        return

    members = await guild_members(bot, guild.id)
    messages = [m async for m in bot.rest.fetch_messages(st.channel_id)]

    await _cleanup_messages(gc, members, messages)

    if st.missing_role_id is not None:
        posted_ids = {
            int(m.author.id)
            for m in messages
            if not m.author.is_bot and not m.is_pinned
        }
        await _reconcile_missing(gc, st, members, posted_ids)


async def _cleanup_messages(
    gc: GuildContext,
    members: list[hikari.Member],
    messages: list[hikari.Message],
) -> None:
    """Delete intro posts from departed members and older duplicate posts.

    `messages` is newest-first, so the first occurrence per author is their keeper.
    """
    member_ids = {int(m.id) for m in members}
    removed_departed: list[str] = []
    removed_dupes: list[str] = []
    seen_authors: set[int] = set()
    # Cache misses are confirmed via REST once each: the member list can be
    # incomplete mid-chunk, and we must never delete a present member's post.
    presence: dict[int, bool] = {}

    for message in messages:
        if message.is_pinned:
            continue

        author_id = int(message.author.id)
        author_name = message.author.username

        present = author_id in member_ids
        if not present:
            if author_id not in presence:
                presence[author_id] = (
                    await guild_member(gc.bot, gc.guild_id, author_id) is not None
                )
            present = presence[author_id]

        if not present:
            if await _try_delete(message, "intro message", gc.name):
                removed_departed.append(author_name)
                logger.info(
                    "Removed departed member's intro", guild=gc.name, user=author_name
                )
        elif author_id in seen_authors:
            if await _try_delete(message, "duplicate intro", gc.name):
                removed_dupes.append(author_name)
                logger.info("Removed duplicate intro", guild=gc.name, user=author_name)
        else:
            seen_authors.add(author_id)

    if removed_departed:
        names = ", ".join(f"**{n}**" for n in removed_departed)
        await gc.log(
            f"🧹 *tidies the den* I nom'd {len(removed_departed)} intro post(s) from members who've left: {names}. 🐉"
        )

    if removed_dupes:
        names = ", ".join(f"**{n}**" for n in removed_dupes)
        await gc.log(
            f"✂️ *snorts smoke* Spotted some sneaky double-intros and trimmed the older one(s) from: {names}. One intro per hoard member! 🐾"
        )


async def _reconcile_missing(
    gc: GuildContext,
    st: IntrosGuildState,
    members: list[hikari.Member],
    posted_ids: set[int],
) -> None:
    """Reconcile the missing-intro role from already-fetched data and log changes."""
    result = await scan_intros(gc, st, members=members, posted_ids=posted_ids)

    logger.info(
        "Intros daily missing sync",
        guild=gc.name,
        missing_count=len(result.missing),
        role_added=len(result.role_added),
        role_removed=len(result.role_removed),
    )

    if not result.role_added and not result.role_removed and not result.role_failed:
        return

    log_bits: list[str] = []
    if result.role_added:
        log_bits.append(
            f"🏷️ *sniffs the air* Pinned the **{st.missing_role_name}** role on "
            f"{len(result.role_added)} member(s) who still owe me an intro."
        )
    if result.role_removed:
        log_bits.append(
            f"🧽 Cleared **{st.missing_role_name}** off {len(result.role_removed)} member(s) "
            f"who'd already posted (the live catch must've missed 'em)."
        )
    if result.role_failed:
        log_bits.append(
            f"⚠️ I couldn't manage **{st.missing_role_name}** — please check my role hierarchy!"
        )
    log_bits.append("🐉")
    await gc.log(" ".join(log_bits))


@loader.task(lightbulb.crontrigger("15 20 * * 6"))
async def intros_weekly_naughty_list(bot: hikari.GatewayBot) -> None:
    """Weekly task: post naughty list of members who haven't introduced themselves."""
    bot = cast("DragonpawBot", bot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.debug("Intros weekly naughty list run", guild_count=len(guilds))

    for guild in guilds:
        try:
            await _naughty_list_guild(bot, guild)
        except Exception:
            logger.exception("Error during intros naughty list", guild=guild.name)


async def _naughty_list_guild(bot: DragonpawBot, guild: hikari.Guild) -> None:
    gc = GuildContext.from_guild(bot, guild)
    st = intros_state.load(int(guild.id))

    if st.channel_id is None:
        return

    bot_st = bot.state(guild.id)
    if not bot_st or not bot_st.general_channel_id:
        return

    result = await scan_intros(gc, st)
    missing_members = result.missing

    logger.info(
        "Intros naughty list",
        guild=guild.name,
        missing_count=len(missing_members),
        role_added=len(result.role_added),
        role_removed=len(result.role_removed),
    )

    role_note = f" with role **{st.required_role_name}**" if st.required_role_id else ""

    if not missing_members:
        try:
            await bot.rest.create_message(
                channel=bot_st.general_channel_id,
                content=(
                    "*does a happy wiggle* 🐉 Everyone in the hoard has posted an introduction — "
                    "I'm so proud of you all! Such good mammals! 🐾"
                ),
            )
        except hikari.HTTPError:
            logger.warning(
                "Failed to post naughty list all-clear",
                guild=guild.name,
                channel_id=int(bot_st.general_channel_id),
            )
        all_clear = ["📋 *happy tail wag* Weekly intros check — everyone's posted!"]
        if result.role_removed:
            all_clear.append(
                f"Cleared **{st.missing_role_name}** off {len(result.role_removed)} straggler(s) who came through."
            )
        all_clear.append("🐉")
        await gc.log(" ".join(all_clear))
        return

    mentions = " ".join(m.mention for m in missing_members)
    role_clause = (
        f" — they're wearing the **{st.missing_role_name}** role until they do"
        if st.missing_role_id
        else ""
    )
    public_lines = [
        f"*squints with clipboard* 🐉 Psst! These lovely folks{role_note} still haven't "
        f"introduced themselves in <#{st.channel_id}>{role_clause}! Give 'em a little nudge! 🐾",
        mentions,
    ]

    try:
        await bot.rest.create_message(
            channel=bot_st.general_channel_id,
            content="\n".join(public_lines),
        )
    except hikari.HTTPError:
        logger.warning(
            "Failed to post naughty list",
            guild=guild.name,
            channel_id=int(bot_st.general_channel_id),
        )
        await gc.log(
            f"⚠️ Couldn't post the weekly intro naughty list in <#{bot_st.general_channel_id}>! 🐉"
        )
        return

    log_bits = [
        f"📋 Weekly intros check — **{len(missing_members)}** member(s){role_note} "
        f"still haven't posted in <#{st.channel_id}>."
    ]
    if st.missing_role_id and not result.role_failed:
        log_bits.append(f"They're all wearing **{st.missing_role_name}**.")
        if result.role_added:
            log_bits.append(f"(Added it to {len(result.role_added)} new straggler(s).)")
        if result.role_removed:
            log_bits.append(
                f"(Cleared it off {len(result.role_removed)} who'd posted.)"
            )
    if result.role_failed:
        log_bits.append(
            f"⚠️ I couldn't manage **{st.missing_role_name}** — please check my role hierarchy!"
        )
    log_bits.append("🐉")
    await gc.log(" ".join(log_bits))
