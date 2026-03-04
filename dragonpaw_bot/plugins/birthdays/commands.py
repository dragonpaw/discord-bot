# -*- coding: utf-8 -*-
from __future__ import annotations

import calendar
import datetime
import logging
import zoneinfo
from typing import TYPE_CHECKING, Any

import hikari
import lightbulb

from dragonpaw_bot import utils
from dragonpaw_bot.colors import SOLARIZED_ORANGE, SOLARIZED_YELLOW
from dragonpaw_bot.plugins.birthdays import state
from dragonpaw_bot.plugins.birthdays.constants import (
    BIRTHDAY_CONFIG_PREFIX,
    BIRTHDAY_PREFIX,
    TIMEZONE_REGIONS,
)
from dragonpaw_bot.plugins.birthdays.models import (
    BirthdayEntry,
    BirthdayGuildConfig,
)

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = logging.getLogger(__name__)

_FEB = 2
_LEAP_DAY = 29
_MAR = 3
_MONTHS_IN_YEAR = 12

# Temporary storage for wishlist URLs during the multi-step set flow.
# Keyed by (guild_id, user_id), cleared after the final save step.
_pending_wishlists: dict[tuple[int, int], str | None] = {}

MONTH_NAMES = [
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


# ---------------------------------------------------------------------------- #
#                                   Helpers                                    #
# ---------------------------------------------------------------------------- #


def _get_bot(ctx: lightbulb.Context) -> DragonpawBot:
    bot: DragonpawBot = ctx.client.app  # type: ignore[assignment]
    return bot


async def _require_guild_owner(
    ctx: lightbulb.Context,
    guild: hikari.Guild | hikari.RESTGuild,
) -> bool:
    """Check if the user is the guild owner. Responds with denial if not. Returns True if allowed."""
    if ctx.user.id == guild.owner_id:
        return True
    logger.warning(
        "G=%r U=%r: Birthday admin command denied, not guild owner",
        guild.name,
        ctx.user.username,
    )
    await ctx.respond(
        "Only the server owner can use this command.",
        flags=hikari.MessageFlag.EPHEMERAL,
    )
    return False


async def _check_permission(
    ctx: lightbulb.Context,
    guild: hikari.Guild | hikari.RESTGuild,
    role_name: str | list[str] | None,
    action: str,
) -> bool:
    """Check permission and respond with denial if lacking. Returns True if allowed."""
    assert ctx.member
    if isinstance(role_name, list):
        allowed = utils.has_any_role_permission(guild, ctx.member, role_name)
        label = (
            "one of the **" + "**, **".join(role_name) + "** roles"
            if role_name
            else "server owner status"
        )
    else:
        allowed = utils.has_permission(guild, ctx.member, role_name)
        label = f"**{role_name}** role" if role_name else "server owner status"
    if allowed:
        return True
    logger.warning(
        "G=%r U=%r: Birthday %s denied, missing %s",
        guild.name,
        ctx.user.username,
        action,
        label,
    )
    await ctx.respond(
        f"You need {label} to use this command.",
        flags=hikari.MessageFlag.EPHEMERAL,
    )
    return False


def _validate_date(month: int, day: int) -> str | None:
    """Validate month/day. Returns error message or None if valid."""
    if month < 1 or month > _MONTHS_IN_YEAR:
        return "Month must be between 1 and 12."
    max_day = _LEAP_DAY if month == _FEB else calendar.monthrange(2000, month)[1]
    if day < 1 or day > max_day:
        return f"Day must be between 1 and {max_day} for {MONTH_NAMES[month]}."
    return None


def _feb29_date(year: int) -> datetime.date:
    """Return Feb 29 of the given year, or Mar 1 if not a leap year."""
    try:
        return datetime.date(year, _FEB, _LEAP_DAY)
    except ValueError:
        return datetime.date(year, _MAR, 1)


def _days_until_birthday(
    month: int, day: int, today: datetime.date | None = None
) -> int:
    """Calculate days until next occurrence of this birthday."""
    if today is None:
        today = datetime.date.today()
    if month == _FEB and day == _LEAP_DAY:
        this_year = _feb29_date(today.year)
    else:
        this_year = datetime.date(today.year, month, day)

    if this_year >= today:
        return (this_year - today).days

    # Next year
    if month == _FEB and day == _LEAP_DAY:
        next_year = _feb29_date(today.year + 1)
    else:
        next_year = datetime.date(today.year + 1, month, day)
    return (next_year - today).days


def is_birthday_on_date(entry: BirthdayEntry, date: datetime.date) -> bool:
    """Check if a birthday entry matches a given date, handling Feb 29."""
    if entry.month == date.month and entry.day == date.day:
        return True
    # Feb 29 birthdays: treat as March 1 in non-leap years
    if entry.month == _FEB and entry.day == _LEAP_DAY:
        if not calendar.isleap(date.year) and date.month == _MAR and date.day == 1:
            return True
    return False


def _validate_timezone(tz_str: str) -> zoneinfo.ZoneInfo | None:
    """Validate and return a ZoneInfo, or None if invalid."""
    try:
        return zoneinfo.ZoneInfo(tz_str)
    except (KeyError, zoneinfo.ZoneInfoNotFoundError):
        return None


def _get_user_tz(entry: BirthdayEntry) -> datetime.tzinfo:
    """Return the user's timezone, falling back to UTC on invalid values."""
    if not entry.timezone:
        return datetime.UTC
    try:
        return zoneinfo.ZoneInfo(entry.timezone)
    except (KeyError, zoneinfo.ZoneInfoNotFoundError):
        logger.warning(
            "U=%d: Invalid timezone %r in state, falling back to UTC",
            entry.user_id,
            entry.timezone,
        )
        return datetime.UTC


def user_local_date(entry: BirthdayEntry) -> datetime.date:
    """Return today's date in the user's configured timezone (default UTC)."""
    return datetime.datetime.now(_get_user_tz(entry)).date()


def user_local_hour(entry: BirthdayEntry) -> int:
    """Return the current hour in the user's configured timezone (default UTC)."""
    return datetime.datetime.now(_get_user_tz(entry)).hour


def user_local_date_offset(entry: BirthdayEntry, *, days: int) -> datetime.date:
    """Return today + offset days in the user's configured timezone."""
    return (
        datetime.datetime.now(_get_user_tz(entry)) + datetime.timedelta(days=days)
    ).date()


# ---------------------------------------------------------------------------- #
#                                  Commands                                    #
# ---------------------------------------------------------------------------- #


def register(birthday_group: lightbulb.Group) -> None:
    """Register all subcommands on the given command group."""
    birthday_group.register(BirthdayStatus)
    birthday_group.register(BirthdaySet)
    birthday_group.register(BirthdayWishlist)
    birthday_group.register(BirthdaySetFor)
    birthday_group.register(BirthdayRemove)
    birthday_group.register(BirthdayRemoveFor)
    birthday_group.register(BirthdayList)
    birthday_group.register(BirthdayConfig)


class BirthdayStatus(
    lightbulb.SlashCommand,
    name="status",
    description="Show your registered birthday and wishlist",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        if not await _check_permission(ctx, guild, cfg.register_role, "status"):
            return
        uid = int(ctx.user.id)
        entry = guild_state.birthdays.get(uid)

        if not entry:
            await ctx.respond(
                "You don't have a birthday registered. Use `/birthday set` to add one.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        local_today = user_local_date(entry)
        days = _days_until_birthday(entry.month, entry.day, today=local_today)
        day_str = (
            "today! 🎂" if days == 0 else f"in **{days}** day{'s' if days != 1 else ''}"
        )
        wishlist = (
            f"[Wishlist]({entry.wishlist_url})"
            if entry.wishlist_url
            else "_No wishlist set_"
        )
        tz_display = entry.timezone or "UTC"

        embed = hikari.Embed(
            title="🎂 Your Birthday",
            description=(
                f"**Date:** {MONTH_NAMES[entry.month]} {entry.day}\n"
                f"**Next birthday:** {day_str}\n"
                f"**Timezone:** {tz_display}\n"
                f"**Wishlist:** {wishlist}"
            ),
            color=SOLARIZED_ORANGE,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


def _month_select_row() -> hikari.api.ComponentBuilder:
    """Build a month select menu."""
    row = hikari.impl.MessageActionRowBuilder()
    select = row.add_text_menu(f"{BIRTHDAY_PREFIX}month")
    for i in range(1, _MONTHS_IN_YEAR + 1):
        select.add_option(MONTH_NAMES[i], str(i))
    select.set_placeholder("Choose your birth month")
    return row


# TODO: Once hikari merges PR #2489 (https://github.com/hikari-py/hikari/pull/2489),
# refactor the entire birthday set flow into a single modal with select menus for
# month, day, region, and timezone instead of the current multi-step message flow.
def _day_modal_rows(
    month: int, existing_wishlist: str | None = None
) -> list[hikari.api.ComponentBuilder]:
    """Build modal action rows for birth day and wishlist URL."""
    max_day = _LEAP_DAY if month == _FEB else calendar.monthrange(2000, month)[1]
    day_row = hikari.impl.ModalActionRowBuilder()
    day_row.add_text_input(
        "day",
        f"Birth day (1–{max_day})",
        placeholder=f"Enter a number from 1 to {max_day}",
        min_length=1,
        max_length=2,
        required=True,
    )
    wishlist_row = hikari.impl.ModalActionRowBuilder()
    wishlist_row.add_text_input(
        "wishlist",
        "Wishlist URL (optional)",
        placeholder="https://example.com/my-wishlist",
        required=False,
        min_length=0,
        max_length=500,
        value=existing_wishlist or "",
    )
    return [day_row, wishlist_row]


class BirthdaySet(
    lightbulb.SlashCommand,
    name="set",
    description="Register or update your birthday",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        if not await _check_permission(ctx, guild, cfg.register_role, "set"):
            return

        await ctx.respond(
            "🎂 **Set Your Birthday**\nStep 1 of 3: Choose your birth month:",
            components=[_month_select_row()],
            flags=hikari.MessageFlag.EPHEMERAL,
        )


class BirthdayWishlist(
    lightbulb.SlashCommand,
    name="wishlist",
    description="View or update your wishlist URL",
):
    url = lightbulb.string("url", "Wishlist URL (omit to view current)", default=None)

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        if not await _check_permission(ctx, guild, cfg.register_role, "wishlist"):
            return

        uid = int(ctx.user.id)
        entry = guild_state.birthdays.get(uid)

        if not entry:
            await ctx.respond(
                "You don't have a birthday registered yet. Use `/birthday set` first.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        if self.url is None:
            current = entry.wishlist_url or "_No wishlist set_"
            await ctx.respond(
                f"Your current wishlist: {current}",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        entry.wishlist_url = self.url
        state.save(guild_state)
        await ctx.respond(
            f"Wishlist updated: {self.url}",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        logger.info(
            "G=%r U=%r: Updated wishlist URL",
            guild_state.guild_name,
            ctx.user.username,
        )


def _region_select_row(month: int, day: int) -> hikari.api.ComponentBuilder:
    """Build the region select menu, encoding month:day in the custom_id."""
    row = hikari.impl.MessageActionRowBuilder()
    select = row.add_text_menu(f"{BIRTHDAY_PREFIX}region:{month}:{day}")
    for region_name in TIMEZONE_REGIONS:
        select.add_option(region_name, region_name)
    select.set_placeholder("Choose your region")
    return row


def _timezone_select_row(month_day: str, region: str) -> hikari.api.ComponentBuilder:
    """Build the timezone select menu, encoding month:day in the custom_id."""
    row = hikari.impl.MessageActionRowBuilder()
    select = row.add_text_menu(f"{BIRTHDAY_PREFIX}tz:{month_day}")
    for tz_id, label in TIMEZONE_REGIONS[region]:
        select.add_option(f"{label} — {tz_id}", tz_id)
    select.set_placeholder(f"Choose your timezone ({region})")
    return row


_RETRY_MSG = "Please try `/birthday set` again."


async def _handle_set_month(interaction: hikari.ComponentInteraction) -> None:
    """Step 1: Month selected → show day input modal."""
    month_str = interaction.values[0] if interaction.values else None
    if not month_str or not month_str.isdigit():
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_UPDATE,
            content=f"Invalid month. {_RETRY_MSG}",
            components=[],
        )
        return
    month = int(month_str)
    if month < 1 or month > _MONTHS_IN_YEAR:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_UPDATE,
            content=f"Invalid month. {_RETRY_MSG}",
            components=[],
        )
        return
    # Pre-fill wishlist URL from existing entry if updating
    existing_wishlist: str | None = None
    if interaction.guild_id:
        guild_state = state.load(int(interaction.guild_id))
        existing = guild_state.birthdays.get(int(interaction.user.id))
        if existing:
            existing_wishlist = existing.wishlist_url

    await interaction.create_modal_response(
        title=f"🎂 Birthday — {MONTH_NAMES[month]}",
        custom_id=f"{BIRTHDAY_PREFIX}day:{month}",
        components=_day_modal_rows(month, existing_wishlist),
    )
    # Clear the month select menu from the original message
    if interaction.message:
        await interaction.edit_message(
            interaction.message,
            content=f"🎂 Month: **{MONTH_NAMES[month]}** — complete the popup to continue.",
            components=[],
        )


def _extract_modal_text_input(
    interaction: hikari.ModalInteraction, custom_id: str
) -> str | None:
    """Extract a text input value from a modal interaction's components."""
    for row in interaction.components:
        for component in row.components:
            if (
                isinstance(component, hikari.TextInputComponent)
                and component.custom_id == custom_id
            ):
                return component.value
    return None


async def _handle_set_day(interaction: hikari.ModalInteraction, field: str) -> None:
    """Step 2: Day and wishlist submitted via modal → show region picker."""
    month_str = field.removeprefix("day:")
    day_str = _extract_modal_text_input(interaction, "day")
    wishlist_url = _extract_modal_text_input(interaction, "wishlist")
    if not month_str.isdigit() or not day_str or not day_str.strip().isdigit():
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content=f"Invalid input. {_RETRY_MSG}",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return
    month = int(month_str)
    day = int(day_str.strip())
    error = _validate_date(month, day)
    if error:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content=f"{error} {_RETRY_MSG}",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    # Stash wishlist URL for the final save step
    if interaction.guild_id:
        key = (int(interaction.guild_id), int(interaction.user.id))
        _pending_wishlists[key] = (
            wishlist_url.strip() if wishlist_url and wishlist_url.strip() else None
        )

    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_CREATE,
        content=f"🎂 **Set Your Birthday**\n"
        f"Date: **{MONTH_NAMES[month]} {day}**\n"
        f"Step 3 of 3: Choose your region:",
        components=[_region_select_row(month, day)],
        flags=hikari.MessageFlag.EPHEMERAL,
    )


async def _handle_set_region(
    interaction: hikari.ComponentInteraction, field: str
) -> None:
    """Step 3: Region selected → show timezone picker."""
    params = field.removeprefix("region:")
    region = interaction.values[0] if interaction.values else None
    if not region or region not in TIMEZONE_REGIONS:
        logger.warning(
            "G=%s U=%r: Invalid timezone region selection: %r",
            interaction.guild_id,
            interaction.user.username,
            region,
        )
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_UPDATE,
            content=f"Invalid region. {_RETRY_MSG}",
            components=[],
        )
        return
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_UPDATE,
        content=f"🎂 **Set Your Birthday**\n"
        f"Region: **{region}**\n"
        f"Now pick your timezone:",
        components=[_timezone_select_row(params, region)],
    )


async def _handle_set_timezone(
    interaction: hikari.ComponentInteraction, field: str
) -> None:
    """Step 4: Timezone selected → save birthday entry."""
    params = field.removeprefix("tz:")
    parts = params.split(":", 1)
    _expected_parts = 2
    if (
        len(parts) != _expected_parts
        or not parts[0].isdigit()
        or not parts[1].isdigit()
    ):
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_UPDATE,
            content=f"Something went wrong. {_RETRY_MSG}",
            components=[],
        )
        return
    month = int(parts[0])
    day = int(parts[1])
    tz_id = interaction.values[0] if interaction.values else None
    if not tz_id or _validate_timezone(tz_id) is None:
        logger.warning(
            "G=%s U=%r: Invalid timezone selection: %r",
            interaction.guild_id,
            interaction.user.username,
            tz_id,
        )
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_UPDATE,
            content=f"Invalid timezone. {_RETRY_MSG}",
            components=[],
        )
        return

    guild_id = interaction.guild_id
    assert guild_id
    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    guild_state = state.load(int(guild_id))
    uid = int(interaction.user.id)
    existing = guild_state.birthdays.get(uid)

    # Use wishlist from the modal if provided, otherwise preserve existing
    key = (int(guild_id), uid)
    wishlist_url = _pending_wishlists.pop(
        key, existing.wishlist_url if existing else None
    )

    entry = BirthdayEntry(
        user_id=uid,
        month=month,
        day=day,
        timezone=tz_id,
        wishlist_url=wishlist_url,
    )
    guild_state.birthdays[uid] = entry
    guild = bot.cache.get_guild(guild_id)
    guild_name = guild.name if guild else str(guild_id)
    guild_state.guild_name = guild_name
    state.save(guild_state)

    action = "updated" if existing else "registered"
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_UPDATE,
        content=f"🎂 Birthday {action}! **{MONTH_NAMES[month]} {day}** "
        f"(timezone: **{tz_id}**)\n"
        f"Your birthday will be announced at midnight in your local time.",
        components=[],
    )
    logger.info(
        "G=%r U=%r: Birthday %s to %s %d (tz=%s)",
        guild_name,
        interaction.user.username,
        action,
        MONTH_NAMES[month],
        day,
        tz_id,
    )
    await utils.log_to_guild(
        bot,
        guild_id,
        f"🎂 {interaction.user.mention} {action} their birthday: "
        f"{MONTH_NAMES[month]} {day}",
    )


async def handle_tz_interaction(interaction: hikari.ComponentInteraction) -> None:
    """Handle the multi-step birthday set flow: month → day → region → timezone."""
    custom_id = interaction.custom_id
    field = custom_id.removeprefix(BIRTHDAY_PREFIX)

    if not interaction.guild_id:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="This command must be used in a server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    if field == "month":
        await _handle_set_month(interaction)
    elif field.startswith("region:"):
        await _handle_set_region(interaction, field)
    elif field.startswith("tz:"):
        await _handle_set_timezone(interaction, field)
    else:
        logger.warning(
            "G=%s U=%r: Unknown birthday interaction field: %r",
            interaction.guild_id,
            interaction.user.username,
            field,
        )
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="Something went wrong. Please try again.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


async def handle_birthday_modal(interaction: hikari.ModalInteraction) -> None:
    """Handle modal submissions for the birthday set flow (day input)."""
    custom_id = interaction.custom_id
    field = custom_id.removeprefix(BIRTHDAY_PREFIX)

    if not interaction.guild_id:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="This command must be used in a server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    if field.startswith("day:"):
        await _handle_set_day(interaction, field)
    else:
        logger.warning(
            "G=%s U=%r: Unknown birthday modal field: %r",
            interaction.guild_id,
            interaction.user.username,
            field,
        )
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="Something went wrong. Please try again.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


class BirthdaySetFor(
    lightbulb.SlashCommand,
    name="set-for",
    description="Register or update a birthday for another user",
):
    user = lightbulb.user("user", "The user to set a birthday for")
    month = lightbulb.integer("month", "Birth month (1-12)")
    day = lightbulb.integer("day", "Birth day (1-31)")
    wishlist_url = lightbulb.string("wishlist_url", "Wishlist URL", default=None)

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await _check_permission(ctx, guild, cfg.manage_role, "set-for"):
            return

        error = _validate_date(self.month, self.day)
        if error:
            await ctx.respond(error, flags=hikari.MessageFlag.EPHEMERAL)
            return

        uid = int(self.user.id)
        existing = guild_state.birthdays.get(uid)
        entry = BirthdayEntry(
            user_id=uid,
            month=self.month,
            day=self.day,
            wishlist_url=self.wishlist_url
            or (existing.wishlist_url if existing else None),
        )
        guild_state.birthdays[uid] = entry
        guild_state.guild_name = guild.name
        state.save(guild_state)

        action = "updated" if existing else "registered"
        await ctx.respond(
            f"🎂 Birthday {action} for {self.user.mention}: "
            f"**{MONTH_NAMES[self.month]} {self.day}**",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        logger.info(
            "G=%r U=%r: Birthday %s for %r to %s %d",
            guild.name,
            ctx.user.username,
            action,
            self.user.username,
            MONTH_NAMES[self.month],
            self.day,
        )
        await utils.log_to_guild(
            bot,
            ctx.guild_id,
            f"🎂 {ctx.user.mention} {action} birthday for {self.user.mention}: "
            f"{MONTH_NAMES[self.month]} {self.day}",
        )


class BirthdayRemove(
    lightbulb.SlashCommand,
    name="remove",
    description="Remove your birthday entry",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        if not await _check_permission(ctx, guild, cfg.register_role, "remove"):
            return

        uid = int(ctx.user.id)

        if uid not in guild_state.birthdays:
            await ctx.respond(
                "You don't have a birthday registered.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        del guild_state.birthdays[uid]
        state.save(guild_state)

        await ctx.respond(
            "Your birthday entry has been removed.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        logger.info(
            "G=%r U=%r: Removed own birthday",
            guild_state.guild_name,
            ctx.user.username,
        )
        await utils.log_to_guild(
            bot,
            ctx.guild_id,
            f"🎂 {ctx.user.mention} removed their birthday entry",
        )


class BirthdayRemoveFor(
    lightbulb.SlashCommand,
    name="remove-for",
    description="Remove another user's birthday entry",
):
    user = lightbulb.user("user", "The user to remove")

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await _check_permission(ctx, guild, cfg.manage_role, "remove-for"):
            return

        uid = int(self.user.id)
        if uid not in guild_state.birthdays:
            await ctx.respond(
                f"{self.user.mention} doesn't have a birthday registered.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        del guild_state.birthdays[uid]
        guild_state.guild_name = guild.name
        state.save(guild_state)

        await ctx.respond(
            f"Birthday entry removed for {self.user.mention}.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        logger.info(
            "G=%r U=%r: Removed birthday for %r",
            guild.name,
            ctx.user.username,
            self.user.username,
        )
        await utils.log_to_guild(
            bot,
            ctx.guild_id,
            f"🎂 {ctx.user.mention} removed birthday entry for {self.user.mention}",
        )


class BirthdayList(
    lightbulb.SlashCommand,
    name="list",
    description="List all registered birthdays",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await _check_permission(ctx, guild, cfg.list_role, "list"):
            return

        if not guild_state.birthdays:
            await ctx.respond(
                "No birthdays registered yet.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        # Group by month, sorted by day
        by_month: dict[int, list[BirthdayEntry]] = {}
        for entry in guild_state.birthdays.values():
            by_month.setdefault(entry.month, []).append(entry)

        lines: list[str] = []
        for month_num in sorted(by_month.keys()):
            entries = sorted(by_month[month_num], key=lambda e: e.day)
            lines.append(f"**{MONTH_NAMES[month_num]}**")
            for entry in entries:
                wishlist = (
                    f" — [Wishlist]({entry.wishlist_url})" if entry.wishlist_url else ""
                )
                lines.append(f"  {entry.day}: <@{entry.user_id}>{wishlist}")

        embed = hikari.Embed(
            title="🎂 Registered Birthdays",
            description="\n".join(lines),
            color=SOLARIZED_ORANGE,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


# ---------------------------------------------------------------------------- #
#                                Config command                                #
# ---------------------------------------------------------------------------- #


def config_embed(cfg: BirthdayGuildConfig) -> hikari.Embed:
    """Build an embed showing current birthday config settings."""
    embed = hikari.Embed(
        title="🎂 Birthday — Configuration",
        description="Use the dropdowns below to change settings. Deselect to clear.",
        color=SOLARIZED_ORANGE,
    )
    embed.add_field(
        name="Register role(s)",
        value=", ".join(f"`{r}`" for r in cfg.register_role)
        if cfg.register_role
        else "_Owner-only_",
        inline=True,
    )
    embed.add_field(
        name="Manage role",
        value=f"`{cfg.manage_role}`" if cfg.manage_role else "_Owner-only_",
        inline=True,
    )
    embed.add_field(
        name="List role",
        value=f"`{cfg.list_role}`" if cfg.list_role else "_Owner-only_",
        inline=True,
    )
    embed.add_field(
        name="Announcement channel",
        value=f"`#{cfg.announcement_channel}`"
        if cfg.announcement_channel
        else "_Disabled_",
        inline=True,
    )
    embed.add_field(
        name="Birthday role",
        value=f"`{cfg.birthday_role}`" if cfg.birthday_role else "_Disabled_",
        inline=True,
    )
    return embed


class _DefaultsActionRow:
    """Wraps a MessageActionRowBuilder to inject default_values into the payload.

    Hikari's select menu builders don't emit ``default_values`` for
    auto-populated selects (role/channel).  Discord's API *does* support
    the field, so we patch it into the built dict.
    """

    def __init__(
        self,
        inner: hikari.api.ComponentBuilder,
        defaults: list[dict[str, str]],
    ) -> None:
        self._inner = inner
        self._defaults = defaults

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to the inner builder."""
        return getattr(self._inner, name)

    def build(self) -> tuple[Any, Any]:
        payload, resources = self._inner.build()
        if self._defaults:
            components = payload.get("components")
            if components and len(components) > 0:
                components[0]["default_values"] = self._defaults
        return payload, resources


ROLE_FIELDS = {"register_role", "manage_role", "list_role", "birthday_role"}
MULTI_ROLE_FIELDS = {"register_role"}


async def config_components(
    bot: DragonpawBot,
    guild_id: hikari.Snowflakeish,
    cfg: BirthdayGuildConfig,
) -> list[Any]:
    """Build the action rows for the config message with current values pre-selected."""
    roles = await bot.rest.fetch_roles(guild_id)
    channels = await bot.rest.fetch_guild_channels(guild_id)
    role_map = {r.name: r.id for r in roles}
    channel_map = {c.name: c.id for c in channels if hasattr(c, "name")}

    rows: list[Any] = []

    # Register role select (multi-select)
    rows.append(
        _DefaultsActionRow(
            bot.rest.build_message_action_row().add_select_menu(
                hikari.ComponentType.ROLE_SELECT_MENU,
                f"{BIRTHDAY_CONFIG_PREFIX}register_role",
                placeholder="Register role(s) (who can self-register)",
                min_values=0,
                max_values=25,
            ),
            [
                {"id": str(role_map[name]), "type": "role"}
                for name in cfg.register_role
                if name in role_map
            ],
        )
    )

    # Manage role select
    rows.append(
        _DefaultsActionRow(
            bot.rest.build_message_action_row().add_select_menu(
                hikari.ComponentType.ROLE_SELECT_MENU,
                f"{BIRTHDAY_CONFIG_PREFIX}manage_role",
                placeholder="Manage role (who can set/remove for others)",
                min_values=0,
                max_values=1,
            ),
            [{"id": str(role_map[cfg.manage_role]), "type": "role"}]
            if cfg.manage_role and cfg.manage_role in role_map
            else [],
        )
    )

    # List role select
    rows.append(
        _DefaultsActionRow(
            bot.rest.build_message_action_row().add_select_menu(
                hikari.ComponentType.ROLE_SELECT_MENU,
                f"{BIRTHDAY_CONFIG_PREFIX}list_role",
                placeholder="List role (who can list all birthdays)",
                min_values=0,
                max_values=1,
            ),
            [{"id": str(role_map[cfg.list_role]), "type": "role"}]
            if cfg.list_role and cfg.list_role in role_map
            else [],
        )
    )

    # Announcement channel select
    rows.append(
        _DefaultsActionRow(
            bot.rest.build_message_action_row().add_channel_menu(
                f"{BIRTHDAY_CONFIG_PREFIX}announcement_channel",
                channel_types=[hikari.ChannelType.GUILD_TEXT],
                placeholder="Announcement channel (birthday posts)",
                min_values=0,
                max_values=1,
            ),
            [{"id": str(channel_map[cfg.announcement_channel]), "type": "channel"}]
            if cfg.announcement_channel and cfg.announcement_channel in channel_map
            else [],
        )
    )

    # Birthday role select
    rows.append(
        _DefaultsActionRow(
            bot.rest.build_message_action_row().add_select_menu(
                hikari.ComponentType.ROLE_SELECT_MENU,
                f"{BIRTHDAY_CONFIG_PREFIX}birthday_role",
                placeholder="Birthday role (auto-assigned on birthday)",
                min_values=0,
                max_values=1,
            ),
            [{"id": str(role_map[cfg.birthday_role]), "type": "role"}]
            if cfg.birthday_role and cfg.birthday_role in role_map
            else [],
        )
    )

    return rows


def resolve_select_value(
    interaction: hikari.ComponentInteraction, field: str
) -> str | None:
    """Extract the name from a role or channel select interaction, or None if cleared."""
    if not interaction.values:
        return None
    snowflake = hikari.Snowflake(interaction.values[0])
    resolved = interaction.resolved
    if not resolved:
        return None
    if field in ROLE_FIELDS:
        role = resolved.roles.get(snowflake) if resolved.roles else None
        return role.name if role else None
    channel = resolved.channels.get(snowflake) if resolved.channels else None
    return channel.name if channel else None


def resolve_multi_role_value(
    interaction: hikari.ComponentInteraction,
) -> list[str]:
    """Extract role names from a multi-select role interaction."""
    if (
        not interaction.values
        or not interaction.resolved
        or not interaction.resolved.roles
    ):
        return []
    names: list[str] = []
    for val in interaction.values:
        snowflake = hikari.Snowflake(val)
        role = interaction.resolved.roles.get(snowflake)
        if role:
            names.append(role.name)
    return names


def display_config_value(v: object) -> str:
    """Format a config value for display in log/audit messages."""
    if isinstance(v, list):
        return ", ".join(v) if v else "None"  # type: ignore[arg-type]
    return v or "None"  # type: ignore[return-value]


async def handle_config_interaction(interaction: hikari.ComponentInteraction) -> None:
    """Handle a component interaction from the config message."""
    custom_id = interaction.custom_id
    if not custom_id.startswith(BIRTHDAY_CONFIG_PREFIX):
        return

    field = custom_id.removeprefix(BIRTHDAY_CONFIG_PREFIX)

    valid_fields = ROLE_FIELDS | {"announcement_channel"}
    if field not in valid_fields:
        logger.warning(
            "U=%r: Unknown birthday config field: %r",
            interaction.user.username,
            field,
        )
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="Unknown setting. Please try again.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    guild_id = interaction.guild_id
    if not guild_id:
        logger.warning("Config interaction missing guild_id, custom_id=%r", custom_id)
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="This command must be used in a server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    # Defer immediately to avoid the 3-second timeout
    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.DEFERRED_MESSAGE_UPDATE,
    )

    # Now do slow REST calls safely
    guild = await bot.rest.fetch_guild(guild_id)
    if interaction.user.id != guild.owner_id:
        await interaction.edit_initial_response(
            content="Only the server owner can change these settings.",
            embeds=[],
            components=[],
        )
        return

    guild_state = state.load(int(guild_id))
    cfg = guild_state.config
    old_value = getattr(cfg, field)

    # Multi-role fields use a separate resolver
    if field in MULTI_ROLE_FIELDS:
        new_value = resolve_multi_role_value(interaction)
    else:
        new_value = resolve_select_value(interaction, field)

    # For channel fields, verify the bot can write to the selected channel
    if new_value and field == "announcement_channel":
        channel_id = hikari.Snowflake(interaction.values[0])
        missing = await utils.check_channel_perms(bot, guild_id, channel_id)
        if missing:
            logger.warning(
                "G=%r U=%r: Birthday config rejected #%s — missing permissions: %s",
                guild.name,
                interaction.user.username,
                new_value,
                ", ".join(missing),
            )
            embed = config_embed(cfg)
            embed.set_footer(
                text=f"Cannot use #{new_value} — missing: {', '.join(missing)}"
            )
            components = await config_components(bot, guild_id, cfg)
            await interaction.edit_initial_response(
                embed=embed,
                components=components,
            )
            return

    if new_value == old_value:
        logger.debug(
            "G=%r U=%r: Birthday setting unchanged: %s = %r",
            guild.name,
            interaction.user.username,
            field,
            new_value,
        )
        embed = config_embed(cfg)
        components = await config_components(bot, guild_id, cfg)
        await interaction.edit_initial_response(
            embed=embed,
            components=components,
        )
        return

    setattr(cfg, field, new_value)
    guild_state.guild_name = guild.name
    state.save(guild_state)

    embed = config_embed(cfg)
    embed.set_footer(text="Settings updated.")
    components = await config_components(bot, guild_id, cfg)
    await interaction.edit_initial_response(
        embed=embed,
        components=components,
    )

    display_old = display_config_value(old_value)
    display_new = display_config_value(new_value)
    logger.info(
        "G=%r U=%r: Birthday setting changed: %s = %r (was %r)",
        guild.name,
        interaction.user.username,
        field,
        display_new,
        display_old,
    )
    await utils.log_to_guild(
        bot,
        guild_id,
        f"⚙️ **Birthday config changed** by {interaction.user.mention}: "
        f"`{field}` changed from `{display_old}` to `{display_new}`",
    )


class BirthdayConfig(
    lightbulb.SlashCommand,
    name="config",
    description="Configure birthday settings for this server (owner only)",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)
        if not await _require_guild_owner(ctx, guild):
            return
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        embed = config_embed(cfg)
        components = await config_components(bot, ctx.guild_id, cfg)
        await ctx.respond(
            embed=embed,
            components=components,
            flags=hikari.MessageFlag.EPHEMERAL,
        )


# ---------------------------------------------------------------------------- #
#                            Announcement embed                                #
# ---------------------------------------------------------------------------- #


def build_announcement_embed(
    member: hikari.Member, entry: BirthdayEntry
) -> hikari.Embed:
    """Build the birthday announcement embed posted in the announcement channel."""
    description = f"🎂 Happy Birthday, {member.mention}! 🎂"
    embed = hikari.Embed(
        title="🎂 Happy Birthday!",
        description=description,
        color=SOLARIZED_YELLOW,
    )
    if entry.wishlist_url:
        embed.add_field(
            name="🎁 Wishlist",
            value=f"[Click here]({entry.wishlist_url})",
            inline=False,
        )
    return embed
