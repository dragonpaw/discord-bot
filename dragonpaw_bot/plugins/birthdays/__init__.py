# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot import utils
from dragonpaw_bot.plugins.birthdays import commands, state
from dragonpaw_bot.plugins.birthdays.constants import (
    BIRTHDAY_CONFIG_PREFIX,
    BIRTHDAY_PREFIX,
)
from dragonpaw_bot.plugins.birthdays.models import (
    BirthdayEntry,
    BirthdayGuildConfig,
)
from dragonpaw_bot.utils import GuildContext, InteractionHandler, ModalHandler

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

__all__ = ["INTERACTION_HANDLERS", "MODAL_HANDLERS"]

logger = structlog.get_logger(__name__)

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    BIRTHDAY_CONFIG_PREFIX: commands.handle_config_interaction,
    BIRTHDAY_PREFIX: commands.handle_tz_interaction,
}

MODAL_HANDLERS: dict[str, ModalHandler] = {
    BIRTHDAY_PREFIX: commands.handle_birthday_modal,
}

loader = lightbulb.Loader()

birthday_group = lightbulb.Group("birthday", "Birthday tracking and announcements")

commands.register(birthday_group)
loader.command(birthday_group)


# ---------------------------------------------------------------------------- #
#                              Daily cron task                                  #
# ---------------------------------------------------------------------------- #


async def announce_birthday(
    gc: GuildContext,
    member: hikari.Member,
    entry: BirthdayEntry,
    cfg: BirthdayGuildConfig,
) -> None:
    """Post announcement and assign birthday role for a member."""
    log = gc.logger.bind(user=member.username)
    # Post announcement embed
    if cfg.announcement_channel:
        channel = await utils.guild_channel_by_name(gc, cfg.announcement_channel)
        if channel:
            try:
                embed = commands.build_announcement_embed(member, entry)
                await gc.bot.rest.create_message(
                    channel=channel.id,
                    content="@everyone",
                    embed=embed,
                    mentions_everyone=True,
                )
                log.info("Posted birthday announcement")
            except hikari.HTTPError as exc:
                log.warning("Failed to post birthday announcement", error=str(exc))
        else:
            log.warning(
                "Announcement channel not found",
                channel=cfg.announcement_channel,
            )

    # Assign birthday role
    if cfg.birthday_role:
        role = await utils.guild_role_by_name(gc, cfg.birthday_role)
        if role:
            try:
                await gc.bot.rest.add_role_to_member(gc.guild_id, member.id, role.id)
                log.info("Assigned birthday role", role=cfg.birthday_role)
            except hikari.HTTPError as exc:
                log.warning("Failed to assign birthday role", error=str(exc))
        else:
            log.warning("Birthday role not found", role=cfg.birthday_role)

    await gc.log(f"🎂 Happy Birthday to {member.mention}!")


async def cleanup_birthday_role(
    gc: GuildContext,
    member: hikari.Member,
    cfg: BirthdayGuildConfig,
) -> None:
    """Remove birthday role from a member whose birthday was yesterday."""
    if not cfg.birthday_role:
        return
    role = await utils.guild_role_by_name(gc, cfg.birthday_role)
    if not role:
        return
    log = gc.logger.bind(user=member.username)
    try:
        await gc.bot.rest.remove_role_from_member(gc.guild_id, member.id, role.id)
        log.info("Removed birthday role (birthday over)")
    except hikari.HTTPError as exc:
        log.warning("Failed to remove birthday role", error=str(exc))


async def send_week_ahead_dm(
    gc: GuildContext,
    guild_state: state.BirthdayGuildState,
    uid: int,
    entry: BirthdayEntry,
) -> None:
    """Send a week-ahead DM reminder to a member, removing them if they left."""
    log = gc.logger.bind(user_id=uid)
    try:
        member = await gc.bot.rest.fetch_member(gc.guild_id, hikari.Snowflake(uid))
    except hikari.NotFoundError:
        log.warning("Member left guild, removing birthday entry")
        del guild_state.birthdays[uid]
        state.save(guild_state)
        return

    member_log = gc.logger.bind(user=member.username)
    wishlist_line = (
        f"Your current wishlist: {entry.wishlist_url}"
        if entry.wishlist_url
        else "You don't have a wishlist set."
    )
    try:
        dm = await member.user.fetch_dm_channel()
        await dm.send(
            f"🎂 Your birthday is in 7 days! ({commands.MONTH_NAMES[entry.month]} {entry.day})\n\n"
            f"{wishlist_line}\n"
            f"Update your wishlist with `/birthday wishlist <url>`"
        )
        member_log.debug("Sent week-ahead birthday DM")
    except hikari.ForbiddenError:
        member_log.warning("Cannot DM user for birthday reminder (DMs disabled)")
    except hikari.HTTPError as exc:
        member_log.warning("Failed to DM birthday reminder", error=str(exc))


async def process_guild_birthdays(gc: GuildContext) -> None:
    """Process birthday announcements, role cleanup, and reminders for one guild.

    Runs hourly. For each user, computes "today" in their local timezone and only
    processes events during the user's midnight hour (hour 0).
    """
    log = gc.logger
    guild_id = int(gc.guild_id)
    guild_state = state.load(guild_id)

    if not guild_state.birthdays:
        log.debug("No birthday entries, skipping")
        return

    guild_state.guild_name = gc.name
    cfg = guild_state.config

    log.debug("Hourly birthday check", entry_count=len(guild_state.birthdays))

    changed = False
    for uid, entry in list(guild_state.birthdays.items()):
        if not entry.timezone:
            continue

        local_hour = commands.user_local_hour(entry)
        if local_hour != 0:
            continue

        local_today = commands.user_local_date(entry)
        local_yesterday = commands.user_local_date_offset(entry, days=-1)
        local_week_ahead = commands.user_local_date_offset(entry, days=7)

        # Birthday announcements
        if commands.is_birthday_on_date(entry, local_today):
            if entry.last_announced == local_today:
                log.debug("Already announced today, skipping", user_id=uid)
                continue
            try:
                member = await gc.bot.rest.fetch_member(
                    gc.guild_id, hikari.Snowflake(uid)
                )
            except hikari.NotFoundError:
                log.warning("Member left guild, removing birthday entry", user_id=uid)
                del guild_state.birthdays[uid]
                state.save(guild_state)
                continue
            await announce_birthday(gc, member, entry, cfg)
            entry.last_announced = local_today
            changed = True
            await asyncio.sleep(1)

        # Role cleanup for yesterday's birthdays
        elif cfg.birthday_role and commands.is_birthday_on_date(entry, local_yesterday):
            try:
                member = await gc.bot.rest.fetch_member(
                    gc.guild_id, hikari.Snowflake(uid)
                )
            except hikari.NotFoundError:
                log.warning(
                    "Member left guild during role cleanup, removing entry",
                    user_id=uid,
                )
                del guild_state.birthdays[uid]
                changed = True
                continue
            await cleanup_birthday_role(gc, member, cfg)

        # Week-ahead DM reminder
        if commands.is_birthday_on_date(entry, local_week_ahead):
            await send_week_ahead_dm(gc, guild_state, uid, entry)
            await asyncio.sleep(1)

    if changed:
        state.save(guild_state)


@loader.task(lightbulb.crontrigger("5 * * * *"))
async def hourly_birthdays(bot: hikari.GatewayBot) -> None:
    """Hourly task: announce birthdays at each user's local midnight."""
    assert isinstance(bot, DragonpawBot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.debug("Birthday hourly run", guild_count=len(guilds))
    for guild in guilds:
        try:
            gc = GuildContext.from_guild(bot, guild)
            await process_guild_birthdays(gc)
        except Exception:
            logger.exception("Error processing birthdays for guild", guild=guild.name)


# ---------------------------------------------------------------------------- #
#                            Member leave cleanup                              #
# ---------------------------------------------------------------------------- #


@loader.listener(hikari.MemberDeleteEvent)
async def on_member_leave(event: hikari.MemberDeleteEvent) -> None:
    """Remove birthday entry when a member leaves the guild."""
    guild_id = int(event.guild_id)
    uid = int(event.user_id)

    try:
        guild_state = state.load(guild_id)
    except Exception:
        logger.exception(
            "Failed to load birthday state for member leave cleanup",
            guild_id=guild_id,
            user_id=uid,
        )
        return

    if uid not in guild_state.birthdays:
        return

    del guild_state.birthdays[uid]

    try:
        state.save(guild_state)
    except Exception:
        logger.exception(
            "Failed to save birthday state after member leave cleanup",
            guild_id=guild_id,
            user_id=uid,
        )
        return

    bot: DragonpawBot = event.app  # type: ignore[assignment]
    guild = event.get_guild()
    guild_name = guild.name if guild else str(event.guild_id)
    logger.warning(
        "Member left guild, removed birthday entry",
        guild=guild_name,
        user_id=uid,
    )

    gs = bot.state(event.guild_id)
    log_channel_id = gs.log_channel_id if gs else None
    gc = GuildContext(
        bot=bot,
        guild_id=event.guild_id,
        name=guild_name,
        log_channel_id=log_channel_id,
    )
    await gc.log(f"🎂 Removed birthday entry for departed member <@{uid}>")
