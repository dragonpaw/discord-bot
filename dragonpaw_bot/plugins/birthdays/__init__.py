# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import hikari
import lightbulb

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
from dragonpaw_bot.utils import InteractionHandler

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

__all__ = ["INTERACTION_HANDLERS"]

logger = logging.getLogger(__name__)

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    BIRTHDAY_CONFIG_PREFIX: commands.handle_config_interaction,
    BIRTHDAY_PREFIX: commands.handle_tz_interaction,
}

loader = lightbulb.Loader()

birthday_group = lightbulb.Group("birthday", "Birthday tracking and announcements")

commands.register(birthday_group)
loader.command(birthday_group)


# ---------------------------------------------------------------------------- #
#                              Daily cron task                                  #
# ---------------------------------------------------------------------------- #


async def announce_birthday(
    bot: DragonpawBot,
    guild: hikari.Guild,
    member: hikari.Member,
    entry: BirthdayEntry,
    cfg: BirthdayGuildConfig,
) -> None:
    """Post announcement and assign birthday role for a member."""
    # Post announcement embed
    if cfg.announcement_channel:
        channel = await utils.guild_channel_by_name(
            bot, guild, cfg.announcement_channel
        )
        if channel:
            try:
                embed = commands.build_announcement_embed(member, entry)
                await bot.rest.create_message(
                    channel=channel.id,
                    content="@everyone",
                    embed=embed,
                    mentions_everyone=True,
                )
                logger.info(
                    "G=%r U=%r: Posted birthday announcement",
                    guild.name,
                    member.username,
                )
            except hikari.HTTPError as exc:
                logger.warning(
                    "G=%r U=%r: Failed to post birthday announcement: %s",
                    guild.name,
                    member.username,
                    exc,
                )
        else:
            logger.warning(
                "G=%r: Announcement channel %r not found",
                guild.name,
                cfg.announcement_channel,
            )

    # Assign birthday role
    if cfg.birthday_role:
        role = await utils.guild_role_by_name(bot, guild, cfg.birthday_role)
        if role:
            try:
                await bot.rest.add_role_to_member(guild.id, member.id, role.id)
                logger.info(
                    "G=%r U=%r: Assigned birthday role %r",
                    guild.name,
                    member.username,
                    cfg.birthday_role,
                )
            except hikari.HTTPError as exc:
                logger.warning(
                    "G=%r U=%r: Failed to assign birthday role: %s",
                    guild.name,
                    member.username,
                    exc,
                )
        else:
            logger.warning(
                "G=%r: Birthday role %r not found",
                guild.name,
                cfg.birthday_role,
            )

    await utils.log_to_guild(
        bot,
        guild.id,
        f"🎂 Happy Birthday to {member.mention}!",
    )


async def cleanup_birthday_role(
    bot: DragonpawBot,
    guild: hikari.Guild,
    member: hikari.Member,
    cfg: BirthdayGuildConfig,
) -> None:
    """Remove birthday role from a member whose birthday was yesterday."""
    if not cfg.birthday_role:
        return
    role = await utils.guild_role_by_name(bot, guild, cfg.birthday_role)
    if not role:
        return
    try:
        await bot.rest.remove_role_from_member(guild.id, member.id, role.id)
        logger.info(
            "G=%r U=%r: Removed birthday role (birthday over)",
            guild.name,
            member.username,
        )
    except hikari.HTTPError as exc:
        logger.warning(
            "G=%r U=%r: Failed to remove birthday role: %s",
            guild.name,
            member.username,
            exc,
        )


async def send_week_ahead_dm(
    bot: DragonpawBot,
    guild: hikari.Guild,
    guild_state: state.BirthdayGuildState,
    uid: int,
    entry: BirthdayEntry,
) -> None:
    """Send a week-ahead DM reminder to a member, removing them if they left."""
    try:
        member = await bot.rest.fetch_member(guild.id, hikari.Snowflake(uid))
    except hikari.NotFoundError:
        logger.warning(
            "G=%r U=%d: Member left guild, removing birthday entry",
            guild.name,
            uid,
        )
        del guild_state.birthdays[uid]
        state.save(guild_state)
        return

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
        logger.debug(
            "G=%r U=%r: Sent week-ahead birthday DM",
            guild.name,
            member.username,
        )
    except hikari.ForbiddenError:
        logger.warning(
            "G=%r U=%r: Cannot DM user for birthday reminder (DMs disabled)",
            guild.name,
            member.username,
        )
    except hikari.HTTPError as exc:
        logger.warning(
            "G=%r U=%r: Failed to DM birthday reminder: %s",
            guild.name,
            member.username,
            exc,
        )


async def process_guild_birthdays(bot: DragonpawBot, guild: hikari.Guild) -> None:
    """Process birthday announcements, role cleanup, and reminders for one guild.

    Runs hourly. For each user, computes "today" in their local timezone and only
    processes events during the user's midnight hour (hour 0).
    """
    guild_id = int(guild.id)
    guild_state = state.load(guild_id)

    if not guild_state.birthdays:
        logger.debug("G=%r: No birthday entries, skipping", guild.name)
        return

    guild_state.guild_name = guild.name
    cfg = guild_state.config

    logger.debug(
        "G=%r: Hourly birthday check for %d entry(ies)",
        guild.name,
        len(guild_state.birthdays),
    )

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
                logger.debug(
                    "G=%r U=%d: Already announced today, skipping",
                    guild.name,
                    uid,
                )
                continue
            try:
                member = await bot.rest.fetch_member(guild.id, hikari.Snowflake(uid))
            except hikari.NotFoundError:
                logger.warning(
                    "G=%r U=%d: Member left guild, removing birthday entry",
                    guild.name,
                    uid,
                )
                del guild_state.birthdays[uid]
                state.save(guild_state)
                continue
            await announce_birthday(bot, guild, member, entry, cfg)
            entry.last_announced = local_today
            changed = True
            await asyncio.sleep(1)

        # Role cleanup for yesterday's birthdays
        elif cfg.birthday_role and commands.is_birthday_on_date(entry, local_yesterday):
            try:
                member = await bot.rest.fetch_member(guild.id, hikari.Snowflake(uid))
            except hikari.NotFoundError:
                logger.warning(
                    "G=%r U=%d: Member left guild during role cleanup, removing entry",
                    guild.name,
                    uid,
                )
                del guild_state.birthdays[uid]
                changed = True
                continue
            await cleanup_birthday_role(bot, guild, member, cfg)

        # Week-ahead DM reminder
        if commands.is_birthday_on_date(entry, local_week_ahead):
            await send_week_ahead_dm(bot, guild, guild_state, uid, entry)
            await asyncio.sleep(1)

    if changed:
        state.save(guild_state)


@loader.task(lightbulb.crontrigger("0 * * * *"))
async def hourly_birthdays(bot: hikari.GatewayBot) -> None:
    """Hourly task: announce birthdays at each user's local midnight."""
    assert isinstance(bot, DragonpawBot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.debug("Birthday hourly run: processing %d guild(s)", len(guilds))
    for guild in guilds:
        try:
            await process_guild_birthdays(bot, guild)
        except Exception:
            logger.exception("Error processing birthdays for guild %r", guild.name)


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
            "G=%d U=%d: Failed to load birthday state for member leave cleanup",
            guild_id,
            uid,
        )
        return

    if uid not in guild_state.birthdays:
        return

    del guild_state.birthdays[uid]

    try:
        state.save(guild_state)
    except Exception:
        logger.exception(
            "G=%d U=%d: Failed to save birthday state after member leave cleanup",
            guild_id,
            uid,
        )
        return

    guild = event.get_guild()
    guild_name = guild.name if guild else str(event.guild_id)
    logger.warning(
        "G=%r U=%d: Member left guild, removed birthday entry",
        guild_name,
        uid,
    )

    bot: DragonpawBot = event.app  # type: ignore[assignment]
    await utils.log_to_guild(
        bot,
        event.guild_id,
        f"🎂 Removed birthday entry for departed member <@{uid}>",
    )
