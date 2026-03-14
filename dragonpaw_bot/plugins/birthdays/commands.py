from __future__ import annotations

import calendar
import datetime
import zoneinfo
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot.colors import SOLARIZED_MAGENTA, SOLARIZED_ORANGE
from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.birthdays import state
from dragonpaw_bot.plugins.birthdays.constants import (
    BIRTHDAY_PREFIX,
    TIMEZONE_REGIONS,
)
from dragonpaw_bot.plugins.birthdays.models import (
    BirthdayEntry,
)

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

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


_WISHLIST_URL_ERROR = (
    "🐉 Wishlists need to be a URL starting with `https://` or `http://`!"
)


def _is_valid_wishlist_url(url: str) -> bool:
    """Check if a string looks like a valid wishlist URL."""
    return url.startswith(("https://", "http://"))


def _clean_wishlist_url(url: str) -> str:
    """Strip query parameters from a wishlist URL.

    Discord's auto-linker breaks on = in query strings, and the params
    (e.g. ?ref=wl_share) are just tracking anyway.
    """
    return url.split("?", maxsplit=1)[0]


def _validate_date(month: int, day: int) -> str | None:
    """Validate month/day. Returns error message or None if valid."""
    if month < 1 or month > _MONTHS_IN_YEAR:
        return "🐉 Month must be between 1 and 12!"
    max_day = _LEAP_DAY if month == _FEB else calendar.monthrange(2000, month)[1]
    if day < 1 or day > max_day:
        return f"🐉 Day must be between 1 and {max_day} for {MONTH_NAMES[month]}!"
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
        today = datetime.datetime.now(tz=datetime.UTC).date()
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
    return (
        entry.month == _FEB
        and entry.day == _LEAP_DAY
        and not calendar.isleap(date.year)
        and date.month == _MAR
        and date.day == 1
    )


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
            "Invalid timezone in state, falling back to UTC",
            user_id=entry.user_id,
            timezone=entry.timezone,
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


class BirthdayStatus(
    lightbulb.SlashCommand,
    name="status",
    description="Show your registered birthday and wishlist",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        if not await gc.check_permission(ctx, cfg.register_role, "status"):
            return
        uid = int(ctx.user.id)
        entry = guild_state.birthdays.get(uid)

        if not entry:
            await ctx.respond(
                "🐉 I don't have your birthday yet! Use `/birthday set` so I know when to celebrate you~ 🎂",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        local_today = user_local_date(entry)
        days = _days_until_birthday(entry.month, entry.day, today=local_today)
        day_str = (
            "today! 🎂" if days == 0 else f"in **{days}** day{'s' if days != 1 else ''}"
        )
        wishlist = (
            f"🎁 <{entry.wishlist_url}>" if entry.wishlist_url else "_No wishlist set_"
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
        gc = GuildContext.from_ctx(ctx)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        if not await gc.check_permission(ctx, cfg.register_role, "set"):
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
        gc = GuildContext.from_ctx(ctx)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        if not await gc.check_permission(ctx, cfg.register_role, "wishlist"):
            return

        uid = int(ctx.user.id)
        entry = guild_state.birthdays.get(uid)

        if not entry:
            await ctx.respond(
                "🐉 You need to register your birthday first! Use `/birthday set`~ 🎂",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        if self.url is None:
            current = entry.wishlist_url or "_No wishlist set_"
            await ctx.respond(
                f"🐉 Your current wishlist: {current}",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        if not _is_valid_wishlist_url(self.url):
            await ctx.respond(
                _WISHLIST_URL_ERROR,
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        entry.wishlist_url = _clean_wishlist_url(self.url)
        state.save(guild_state)
        await ctx.respond(
            f"🐉 Wishlist updated! {self.url} 🎁",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        logger.info(
            "Updated wishlist URL",
            guild=guild_state.guild_name,
            user=ctx.user.username,
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

    # Validate and stash wishlist URL for the final save step
    cleaned_wishlist: str | None = None
    if wishlist_url and wishlist_url.strip():
        cleaned_wishlist = _clean_wishlist_url(wishlist_url.strip())
        if not _is_valid_wishlist_url(cleaned_wishlist):
            await interaction.create_initial_response(
                response_type=hikari.ResponseType.MESSAGE_CREATE,
                content=f"{_WISHLIST_URL_ERROR} {_RETRY_MSG}",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

    if interaction.guild_id:
        key = (int(interaction.guild_id), int(interaction.user.id))
        _pending_wishlists[key] = cleaned_wishlist

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
        logger.warning("Invalid timezone region selection", region=region)
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
        logger.warning("Invalid timezone selection", timezone=tz_id)
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
        content=f"🐉🎂 Birthday {action}! **{MONTH_NAMES[month]} {day}** "
        f"(timezone: **{tz_id}**)\n"
        f"I'll announce it at midnight in your local time — I can't wait to celebrate! ✨",
        components=[],
    )
    logger.info(
        "Birthday registered or updated",
        action=action,
        month=MONTH_NAMES[month],
        day=day,
        timezone=tz_id,
    )
    gc = GuildContext.from_interaction(interaction)
    await gc.log(
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
        logger.warning("Unknown birthday interaction field", field=field)
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
        logger.warning("Unknown birthday modal field", field=field)
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
        gc = GuildContext.from_ctx(ctx)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await gc.check_permission(ctx, cfg.manage_role, "set-for"):
            return

        error = _validate_date(self.month, self.day)
        if error:
            await ctx.respond(error, flags=hikari.MessageFlag.EPHEMERAL)
            return

        if self.wishlist_url and not _is_valid_wishlist_url(self.wishlist_url):
            await ctx.respond(
                _WISHLIST_URL_ERROR,
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        uid = int(self.user.id)
        existing = guild_state.birthdays.get(uid)
        entry = BirthdayEntry(
            user_id=uid,
            month=self.month,
            day=self.day,
            wishlist_url=_clean_wishlist_url(self.wishlist_url)
            if self.wishlist_url
            else (existing.wishlist_url if existing else None),
        )
        guild_state.birthdays[uid] = entry
        guild_state.guild_name = gc.name
        state.save(guild_state)

        action = "updated" if existing else "registered"
        await ctx.respond(
            f"🐉🎂 Birthday {action} for {self.user.mention}: "
            f"**{MONTH_NAMES[self.month]} {self.day}**",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        logger.info(
            "Birthday set for other user",
            guild=gc.name,
            user=ctx.user.username,
            action=action,
            target=self.user.username,
            month=MONTH_NAMES[self.month],
            day=self.day,
        )
        await gc.log(
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
        gc = GuildContext.from_ctx(ctx)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        if not await gc.check_permission(ctx, cfg.register_role, "remove"):
            return

        uid = int(ctx.user.id)

        if uid not in guild_state.birthdays:
            await ctx.respond(
                "🐉 You don't have a birthday registered! Nothing to remove~ 🐾",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        del guild_state.birthdays[uid]
        state.save(guild_state)

        await ctx.respond(
            "🐉 Your birthday entry has been removed. I'll miss celebrating you! 🐾",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        logger.info(
            "Removed own birthday",
            guild=gc.name,
            user=ctx.user.username,
        )
        await gc.log(f"🎂 {ctx.user.mention} removed their birthday entry")


class BirthdayRemoveFor(
    lightbulb.SlashCommand,
    name="remove-for",
    description="Remove another user's birthday entry",
):
    user = lightbulb.user("user", "The user to remove")

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await gc.check_permission(ctx, cfg.manage_role, "remove-for"):
            return

        uid = int(self.user.id)
        if uid not in guild_state.birthdays:
            await ctx.respond(
                f"🐉 {self.user.mention} doesn't have a birthday registered!",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        del guild_state.birthdays[uid]
        guild_state.guild_name = gc.name
        state.save(guild_state)

        await ctx.respond(
            f"🐉 Birthday entry removed for {self.user.mention} 🐾",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        logger.info(
            "Removed birthday for other user",
            guild=gc.name,
            user=ctx.user.username,
            target=self.user.username,
        )
        await gc.log(
            f"🎂 {ctx.user.mention} removed birthday entry for {self.user.mention}"
        )


class BirthdayList(
    lightbulb.SlashCommand,
    name="list",
    description="List all registered birthdays",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await gc.check_permission(ctx, cfg.list_role, "list"):
            return

        if not guild_state.birthdays:
            await ctx.respond(
                "🐉 No birthdays registered yet! Tell your friends to use `/birthday set`~ 🎂",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        # Identify today's birthdays and the next upcoming one
        today = datetime.datetime.now(tz=datetime.UTC).date()
        today_md = (today.month, today.day)
        today_keys: set[tuple[int, int]] = set()

        def _days_until_future(entry: BirthdayEntry) -> int:
            """Days from today until this birthday next occurs (excluding today)."""
            try:
                this_year = today.replace(month=entry.month, day=entry.day)
            except ValueError:
                # Feb 29 in a non-leap year — use Feb 28
                this_year = today.replace(month=entry.month, day=28)
            if (entry.month, entry.day) <= today_md:
                this_year = this_year.replace(year=today.year + 1)
            return (this_year - today).days

        for entry in guild_state.birthdays.values():
            if (entry.month, entry.day) == today_md:
                today_keys.add(today_md)

        next_entry = min(guild_state.birthdays.values(), key=_days_until_future)
        next_key = (next_entry.month, next_entry.day)

        # Group by month, sorted by day
        by_month: dict[int, list[BirthdayEntry]] = {}
        for entry in guild_state.birthdays.values():
            by_month.setdefault(entry.month, []).append(entry)

        # Build one embed per month
        month_embeds: list[hikari.Embed] = []
        for month_num in sorted(by_month.keys()):
            entries = sorted(by_month[month_num], key=lambda e: e.day)
            lines: list[str] = []
            for entry in entries:
                tz = f" ({entry.timezone})" if entry.timezone else ""
                entry_md = (entry.month, entry.day)
                if entry_md in today_keys:
                    marker = "🎂"
                elif entry_md == next_key:
                    marker = "⭐"
                else:
                    marker = "  "
                wishlist = (
                    f" 🎁 [wishlist]({_clean_wishlist_url(entry.wishlist_url)})"
                    if entry.wishlist_url
                    else ""
                )
                lines.append(f"{marker} {entry.day}: <@{entry.user_id}>{tz}{wishlist}")
            month_embeds.append(
                hikari.Embed(
                    title=MONTH_NAMES[month_num],
                    description="\n".join(lines),
                    color=SOLARIZED_ORANGE,
                )
            )

        # Title embed with legend
        legend_parts = []
        if today_keys:
            legend_parts.append("🎂 = birthday today!")
        legend_parts.append("⭐ = next upcoming")
        title_embed = hikari.Embed(
            title="🎂 Registered Birthdays",
            description=" · ".join(legend_parts),
            color=SOLARIZED_ORANGE,
        )

        # Discord allows up to 10 embeds per message
        first_batch = month_embeds[:9]
        second_batch = month_embeds[9:]

        await ctx.respond(
            embeds=[title_embed, *first_batch],
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        if second_batch:
            await ctx.respond(
                embeds=second_batch,
                flags=hikari.MessageFlag.EPHEMERAL,
            )


# ---------------------------------------------------------------------------- #
#                            Announcement embed                                #
# ---------------------------------------------------------------------------- #


def build_announcement_embed(
    member: hikari.Member, entry: BirthdayEntry
) -> hikari.Embed:
    """Build the birthday announcement embed posted in the announcement channel."""
    if entry.wishlist_url:
        description = (
            f"*races into the channel, skidding on tiny claws*\n\n"
            f"EVERYONE!! It's {member.mention}'s BIRTHDAY today!! 🎂🎉\n\n"
            f"They are *definitely* on the naughty list this year, "
            f"so let's spoil them rotten! 👀🎁"
        )
        embed = hikari.Embed(
            title="🔥 A BIRTHDAY! A BIRTHDAY!",
            description=description,
            color=SOLARIZED_MAGENTA,
        )
        embed.add_field(
            name="🎁 Spoil Them Here!",
            value=_clean_wishlist_url(entry.wishlist_url),
            inline=False,
        )
    else:
        description = (
            f"*races into the channel, skidding on tiny claws*\n\n"
            f"EVERYONE!! It's {member.mention}'s BIRTHDAY today!! 🎂🎉\n\n"
            f"They are *definitely* on the naughty list this year — "
            f"and they didn't even set up a wishlist, "
            f"so you'll just have to surprise them! 🐉💖"
        )
        embed = hikari.Embed(
            title="🔥 A BIRTHDAY! A BIRTHDAY!",
            description=description,
            color=SOLARIZED_MAGENTA,
        )
    return embed
