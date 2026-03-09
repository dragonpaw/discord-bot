# -*- coding: utf-8 -*-
"""Slash commands: /config birthday settings"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import hikari
import lightbulb
import structlog

from dragonpaw_bot import utils
from dragonpaw_bot.colors import SOLARIZED_ORANGE
from dragonpaw_bot.plugins.birthdays import state
from dragonpaw_bot.plugins.birthdays.constants import BIRTHDAY_CONFIG_PREFIX
from dragonpaw_bot.plugins.birthdays.models import BirthdayGuildConfig
from dragonpaw_bot.utils import GuildContext

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------- #
#                                   Helpers                                    #
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


# ---------------------------------------------------------------------------- #
#                            Interaction handler                               #
# ---------------------------------------------------------------------------- #


async def handle_config_interaction(interaction: hikari.ComponentInteraction) -> None:
    """Handle a component interaction from the config message."""
    custom_id = interaction.custom_id
    if not custom_id.startswith(BIRTHDAY_CONFIG_PREFIX):
        return

    field = custom_id.removeprefix(BIRTHDAY_CONFIG_PREFIX)

    valid_fields = ROLE_FIELDS | {"announcement_channel"}
    if field not in valid_fields:
        logger.warning("Unknown birthday config field", field=field)
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="Unknown setting. Please try again.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    guild_id = interaction.guild_id
    if not guild_id:
        logger.warning("Config interaction missing guild_id", custom_id=custom_id)
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
                "Birthday config rejected — missing channel permissions",
                channel=new_value,
                missing=", ".join(missing),
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
        logger.debug("Birthday setting unchanged", field=field, value=new_value)
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
        "Birthday setting changed",
        field=field,
        new_value=display_new,
        old_value=display_old,
    )
    gc = GuildContext.from_interaction(interaction)
    await gc.log(
        f"⚙️ **Birthday config changed** by {interaction.user.mention}: "
        f"`{field}` changed from `{display_old}` to `{display_new}`",
    )


# ---------------------------------------------------------------------------- #
#                                   Commands                                   #
# ---------------------------------------------------------------------------- #


class BirthdaySettings(
    lightbulb.SlashCommand,
    name="settings",
    description="Configure birthday settings for this server (owner only)",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)
        if not await gc.require_owner(ctx):
            return
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        embed = config_embed(cfg)
        components = await config_components(gc.bot, ctx.guild_id, cfg)
        await ctx.respond(
            embed=embed,
            components=components,
            flags=hikari.MessageFlag.EPHEMERAL,
        )


def register(subgroup: lightbulb.SubGroup) -> None:
    subgroup.register(BirthdaySettings)
