# -*- coding: utf-8 -*-
from __future__ import annotations

import calendar
import datetime
import logging
from typing import TYPE_CHECKING, Any

import hikari
import lightbulb

from dragonpaw_bot import utils
from dragonpaw_bot.colors import SOLARIZED_ORANGE, SOLARIZED_YELLOW
from dragonpaw_bot.plugins.birthdays import state
from dragonpaw_bot.plugins.birthdays.constants import BIRTHDAY_CONFIG_PREFIX
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
    role_name: str | None,
    action: str,
) -> bool:
    """Check permission and respond with denial if lacking. Returns True if allowed."""
    assert ctx.member
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


def _days_until_birthday(month: int, day: int) -> int:
    """Calculate days until next occurrence of this birthday."""
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


def _is_birthday_on_date(entry: BirthdayEntry, date: datetime.date) -> bool:
    """Check if a birthday entry matches a given date, handling Feb 29."""
    if entry.month == date.month and entry.day == date.day:
        return True
    # Feb 29 birthdays: treat as March 1 in non-leap years
    if entry.month == _FEB and entry.day == _LEAP_DAY:
        if not calendar.isleap(date.year) and date.month == _MAR and date.day == 1:
            return True
    return False


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
        guild_state = state.load(int(ctx.guild_id))
        uid = int(ctx.user.id)
        entry = guild_state.birthdays.get(uid)

        if not entry:
            await ctx.respond(
                "You don't have a birthday registered. Use `/birthday set` to add one.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        days = _days_until_birthday(entry.month, entry.day)
        day_str = (
            "today! 🎂" if days == 0 else f"in **{days}** day{'s' if days != 1 else ''}"
        )
        wishlist = (
            f"[Wishlist]({entry.wishlist_url})"
            if entry.wishlist_url
            else "_No wishlist set_"
        )

        embed = hikari.Embed(
            title="🎂 Your Birthday",
            description=(
                f"**Date:** {MONTH_NAMES[entry.month]} {entry.day}\n"
                f"**Next birthday:** {day_str}\n"
                f"**Wishlist:** {wishlist}"
            ),
            color=SOLARIZED_ORANGE,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


class BirthdaySet(
    lightbulb.SlashCommand,
    name="set",
    description="Register or update your birthday",
):
    month = lightbulb.integer("month", "Birth month (1-12)")
    day = lightbulb.integer("day", "Birth day (1-31)")
    wishlist_url = lightbulb.string("wishlist_url", "Wishlist URL", default=None)

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        error = _validate_date(self.month, self.day)
        if error:
            await ctx.respond(error, flags=hikari.MessageFlag.EPHEMERAL)
            return

        guild_state = state.load(int(ctx.guild_id))
        uid = int(ctx.user.id)
        existing = guild_state.birthdays.get(uid)

        entry = BirthdayEntry(
            user_id=uid,
            month=self.month,
            day=self.day,
            wishlist_url=self.wishlist_url
            or (existing.wishlist_url if existing else None),
        )
        guild_state.birthdays[uid] = entry
        bot = _get_bot(ctx)
        guild = bot.cache.get_guild(ctx.guild_id)
        if guild:
            guild_state.guild_name = guild.name
        state.save(guild_state)

        action = "updated" if existing else "registered"
        await ctx.respond(
            f"🎂 Birthday {action}: **{MONTH_NAMES[self.month]} {self.day}**",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        logger.info(
            "G=%r U=%r: Birthday %s to %s %d",
            guild_state.guild_name,
            ctx.user.username,
            action,
            MONTH_NAMES[self.month],
            self.day,
        )
        await utils.log_to_guild(
            bot,
            ctx.guild_id,
            f"🎂 {ctx.user.mention} {action} their birthday: "
            f"{MONTH_NAMES[self.month]} {self.day}",
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
        guild_state = state.load(int(ctx.guild_id))
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
        guild_state = state.load(int(ctx.guild_id))
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

        bot = _get_bot(ctx)
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


def _config_embed(cfg: BirthdayGuildConfig) -> hikari.Embed:
    """Build an embed showing current birthday config settings."""
    embed = hikari.Embed(
        title="🎂 Birthday — Configuration",
        description="Use the dropdowns below to change settings. Deselect to clear.",
        color=SOLARIZED_ORANGE,
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
            payload["components"][0]["default_values"] = self._defaults
        return payload, resources


ROLE_FIELDS = {"manage_role", "list_role", "birthday_role"}


async def _config_components(
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

    # Row 1: Manage role select
    row1 = bot.rest.build_message_action_row()
    row1.add_select_menu(
        hikari.ComponentType.ROLE_SELECT_MENU,
        f"{BIRTHDAY_CONFIG_PREFIX}manage_role",
        placeholder="Manage role (who can set/remove for others)",
        min_values=0,
        max_values=1,
    )
    defaults1 = (
        [{"id": str(role_map[cfg.manage_role]), "type": "role"}]
        if cfg.manage_role and cfg.manage_role in role_map
        else []
    )
    rows.append(_DefaultsActionRow(row1, defaults1))

    # Row 2: List role select
    row2 = bot.rest.build_message_action_row()
    row2.add_select_menu(
        hikari.ComponentType.ROLE_SELECT_MENU,
        f"{BIRTHDAY_CONFIG_PREFIX}list_role",
        placeholder="List role (who can list all birthdays)",
        min_values=0,
        max_values=1,
    )
    defaults2 = (
        [{"id": str(role_map[cfg.list_role]), "type": "role"}]
        if cfg.list_role and cfg.list_role in role_map
        else []
    )
    rows.append(_DefaultsActionRow(row2, defaults2))

    # Row 3: Announcement channel select
    row3 = bot.rest.build_message_action_row()
    row3.add_channel_menu(
        f"{BIRTHDAY_CONFIG_PREFIX}announcement_channel",
        channel_types=[hikari.ChannelType.GUILD_TEXT],
        placeholder="Announcement channel (birthday posts)",
        min_values=0,
        max_values=1,
    )
    defaults3 = (
        [{"id": str(channel_map[cfg.announcement_channel]), "type": "channel"}]
        if cfg.announcement_channel and cfg.announcement_channel in channel_map
        else []
    )
    rows.append(_DefaultsActionRow(row3, defaults3))

    # Row 4: Birthday role select
    row4 = bot.rest.build_message_action_row()
    row4.add_select_menu(
        hikari.ComponentType.ROLE_SELECT_MENU,
        f"{BIRTHDAY_CONFIG_PREFIX}birthday_role",
        placeholder="Birthday role (auto-assigned on birthday)",
        min_values=0,
        max_values=1,
    )
    defaults4 = (
        [{"id": str(role_map[cfg.birthday_role]), "type": "role"}]
        if cfg.birthday_role and cfg.birthday_role in role_map
        else []
    )
    rows.append(_DefaultsActionRow(row4, defaults4))

    return rows


def _resolve_select_value(
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


async def handle_config_interaction(interaction: hikari.ComponentInteraction) -> None:
    """Handle a component interaction from the config message."""
    custom_id = interaction.custom_id
    if not custom_id.startswith(BIRTHDAY_CONFIG_PREFIX):
        return

    field = custom_id.removeprefix(BIRTHDAY_CONFIG_PREFIX)

    guild_id = interaction.guild_id
    if not guild_id:
        logger.warning("Config interaction missing guild_id, custom_id=%r", custom_id)
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="This command must be used in a server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    # Only allow the guild owner
    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    guild = await bot.rest.fetch_guild(guild_id)
    if interaction.user.id != guild.owner_id:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="Only the server owner can change these settings.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    guild_state = state.load(int(guild_id))
    cfg = guild_state.config
    old_value = getattr(cfg, field)

    new_value = _resolve_select_value(interaction, field)

    # For channel fields, verify the bot can write to the selected channel
    if new_value and field == "announcement_channel":
        channel_id = hikari.Snowflake(interaction.values[0])
        missing = await utils.check_channel_perms(bot, guild_id, channel_id)
        if missing:
            missing_str = ", ".join(f"**{p}**" for p in missing)
            await interaction.create_initial_response(
                response_type=hikari.ResponseType.MESSAGE_CREATE,
                content=(
                    f"I can't use #{new_value} — I'm missing these permissions: "
                    f"{missing_str}. Please fix the channel permissions and try again."
                ),
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            logger.warning(
                "G=%r U=%r: Birthday config rejected #%s — missing permissions: %s",
                guild.name,
                interaction.user.username,
                new_value,
                ", ".join(missing),
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
        embed = _config_embed(cfg)
        components = await _config_components(bot, guild_id, cfg)
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_UPDATE,
            embed=embed,
            components=components,
        )
        return

    # Update state and prepare response before responding
    setattr(cfg, field, new_value)
    guild_state.guild_name = guild.name
    embed = _config_embed(cfg)
    embed.set_footer(text="Settings updated.")
    components = await _config_components(bot, guild_id, cfg)

    # Respond first (within 3-second timeout)
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_UPDATE,
        embed=embed,
        components=components,
    )

    # Then do slow work: save state and log
    state.save(guild_state)
    display_old = old_value or "None"
    display_new = new_value or "None"
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
        embed = _config_embed(cfg)
        components = await _config_components(bot, ctx.guild_id, cfg)
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
