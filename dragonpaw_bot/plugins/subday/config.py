"""Slash commands: /config subday settings|prize-roles|prizes"""

from __future__ import annotations

from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot.colors import SOLARIZED_VIOLET
from dragonpaw_bot.context import GuildContext, check_channel_perms
from dragonpaw_bot.plugins.subday import state
from dragonpaw_bot.plugins.subday.constants import (
    MILESTONE_WEEKS,
    SUBDAY_CFG_ROLE_PREFIX,
    SUBDAY_CONFIG_PREFIX,
)

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot
    from dragonpaw_bot.plugins.subday.models import SubDayGuildConfig

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------- #
#                                   Helpers                                    #
# ---------------------------------------------------------------------------- #


def _config_embed(cfg: SubDayGuildConfig) -> hikari.Embed:
    """Build an embed showing current SubDay config settings."""
    embed = hikari.Embed(
        title="Where I am Led — Configuration",
        description="Use the dropdowns below to change settings. Deselect to clear.",
        color=SOLARIZED_VIOLET,
    )
    embed.add_field(
        name="Enroll role(s)",
        value=", ".join(f"`{r}`" for r in cfg.enroll_role)
        if cfg.enroll_role
        else "_Owner-only_",
        inline=True,
    )
    embed.add_field(
        name="Complete role",
        value=f"`{cfg.complete_role}`" if cfg.complete_role else "_Owner-only_",
        inline=True,
    )
    embed.add_field(
        name="Backfill role",
        value=f"`{cfg.backfill_role}`" if cfg.backfill_role else "_Owner-only_",
        inline=True,
    )
    embed.add_field(
        name="Achievements channel",
        value=f"`#{cfg.achievements_channel}`"
        if cfg.achievements_channel
        else "_Disabled_",
        inline=True,
    )
    roles = cfg.milestone_roles()
    role_lines = [
        f"**Week {w}:** `{r}`" if r else f"**Week {w}:** _None_"
        for w, r in sorted(roles.items())
    ]
    embed.add_field(
        name="Milestone roles",
        value="\n".join(role_lines),
        inline=False,
    )
    prizes = cfg.milestone_prizes()
    prize_lines = [f"**Week {w}:** {p}" for w, p in sorted(prizes.items())]
    embed.add_field(
        name="Milestone prizes",
        value="\n".join(prize_lines),
        inline=False,
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

    def build(self) -> tuple[dict, list]:
        payload, resources = self._inner.build()
        if self._defaults:
            payload["components"][0]["default_values"] = self._defaults
        return payload, resources


async def _config_components(
    bot: DragonpawBot,
    guild_id: hikari.Snowflakeish,
    cfg: SubDayGuildConfig,
) -> list[hikari.api.ComponentBuilder]:
    """Build the action rows for the config message with current values pre-selected."""
    roles = await bot.rest.fetch_roles(guild_id)
    channels = await bot.rest.fetch_guild_channels(guild_id)
    role_map = {r.name: r.id for r in roles}
    channel_map = {c.name: c.id for c in channels if hasattr(c, "name")}

    rows: list[hikari.api.ComponentBuilder] = []

    # Enroll role select (multi-select)
    rows.append(
        _DefaultsActionRow(
            bot.rest.build_message_action_row().add_select_menu(
                hikari.ComponentType.ROLE_SELECT_MENU,
                f"{SUBDAY_CONFIG_PREFIX}enroll_role",
                placeholder="Enroll role(s) (who can sign up)",
                min_values=0,
                max_values=25,
            ),
            [
                {"id": str(role_map[name]), "type": "role"}
                for name in cfg.enroll_role
                if name in role_map
            ],
        )
    )

    # Complete role select
    rows.append(
        _DefaultsActionRow(
            bot.rest.build_message_action_row().add_select_menu(
                hikari.ComponentType.ROLE_SELECT_MENU,
                f"{SUBDAY_CONFIG_PREFIX}complete_role",
                placeholder="Complete role (who can complete/list/remove)",
                min_values=0,
                max_values=1,
            ),
            [{"id": str(role_map[cfg.complete_role]), "type": "role"}]
            if cfg.complete_role and cfg.complete_role in role_map
            else [],
        )
    )

    # Backfill role select
    rows.append(
        _DefaultsActionRow(
            bot.rest.build_message_action_row().add_select_menu(
                hikari.ComponentType.ROLE_SELECT_MENU,
                f"{SUBDAY_CONFIG_PREFIX}backfill_role",
                placeholder="Backfill role (who can backfill weeks)",
                min_values=0,
                max_values=1,
            ),
            [{"id": str(role_map[cfg.backfill_role]), "type": "role"}]
            if cfg.backfill_role and cfg.backfill_role in role_map
            else [],
        )
    )

    # Achievements channel select
    rows.append(
        _DefaultsActionRow(
            bot.rest.build_message_action_row().add_channel_menu(
                f"{SUBDAY_CONFIG_PREFIX}achievements_channel",
                channel_types=[hikari.ChannelType.GUILD_TEXT],
                placeholder="Achievements channel (public posts)",
                min_values=0,
                max_values=1,
            ),
            [{"id": str(channel_map[cfg.achievements_channel]), "type": "channel"}]
            if cfg.achievements_channel and cfg.achievements_channel in channel_map
            else [],
        )
    )

    return rows


ROLE_FIELDS = {
    "enroll_role",
    "complete_role",
    "backfill_role",
    "role_13",
    "role_26",
    "role_39",
    "role_52",
}


MULTI_ROLE_FIELDS = {"enroll_role"}


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


def _resolve_multi_role_value(
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


def _display_config_value(v: object) -> str:
    """Format a config value for display in log/audit messages."""
    if isinstance(v, list):
        return ", ".join(v) if v else "None"
    return v or "None"  # type: ignore[return-value]


async def _reject_missing_perms(
    interaction: hikari.ComponentInteraction,
    bot: DragonpawBot,
    guild_id: hikari.Snowflakeish,
    guild_state: state.SubDayGuildState,
    channel_name: str,
) -> bool:
    """Check channel perms and send an error response if missing. Returns True if rejected."""
    channel_id = hikari.Snowflake(interaction.values[0])
    missing = await check_channel_perms(bot, guild_id, channel_id)
    if not missing:
        return False
    missing_str = ", ".join(f"**{p}**" for p in missing)
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_CREATE,
        content=(
            f"I can't use #{channel_name} — I'm missing these permissions: "
            f"{missing_str}. Please fix the channel permissions and try again."
        ),
        flags=hikari.MessageFlag.EPHEMERAL,
    )
    logger.warning(
        "SubDay config rejected, missing channel permissions",
        guild=guild_state.guild_name,
        user=interaction.user.username,
        channel=channel_name,
        missing_perms=", ".join(missing),
    )
    return True


# ---------------------------------------------------------------------------- #
#                            Interaction handler                               #
# ---------------------------------------------------------------------------- #


async def handle_config_interaction(interaction: hikari.ComponentInteraction) -> None:
    """Handle a component interaction from the config or prize-roles message."""
    custom_id = interaction.custom_id

    if custom_id.startswith(SUBDAY_CFG_ROLE_PREFIX):
        field = custom_id.removeprefix(SUBDAY_CFG_ROLE_PREFIX)
        embed_builder = _prize_roles_embed
        components_builder = _prize_roles_components
    elif custom_id.startswith(SUBDAY_CONFIG_PREFIX):
        field = custom_id.removeprefix(SUBDAY_CONFIG_PREFIX)
        embed_builder = _config_embed
        components_builder = _config_components
    else:
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

    # Only allow the guild owner
    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    guild = await bot.rest.fetch_guild(guild_id)
    if interaction.user.id != guild.owner_id:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="*guards the treasure* 🐉 Only the server owner can change these settings!",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    guild_state = state.load(int(guild_id))
    cfg = guild_state.config
    old_value = getattr(cfg, field)

    # Multi-role fields use a separate resolver
    if field in MULTI_ROLE_FIELDS:
        new_value = _resolve_multi_role_value(interaction)
    else:
        new_value = _resolve_select_value(interaction, field)

    # For channel fields, verify the bot can write to the selected channel
    if (
        new_value
        and field == "achievements_channel"
        and await _reject_missing_perms(
            interaction, bot, guild_id, guild_state, new_value
        )
    ):
        return

    if new_value == old_value:
        logger.debug(
            "SubDay setting unchanged",
            field=field,
            value=new_value,
        )
        embed = embed_builder(cfg)
        components = await components_builder(bot, guild_id, cfg)
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_UPDATE,
            embed=embed,
            components=components,
        )
        return

    setattr(cfg, field, new_value)
    guild_state.guild_name = guild.name
    state.save(guild_state)

    display_old = _display_config_value(old_value)
    display_new = _display_config_value(new_value)
    logger.info(
        "SubDay setting changed",
        field=field,
        old_value=display_old,
        new_value=display_new,
    )

    gc = GuildContext.from_interaction(interaction)
    await gc.log(
        f"⚙️ **SubDay config changed** by {interaction.user.mention}: "
        f"`{field}` changed from `{display_old}` to `{display_new}`",
    )

    embed = embed_builder(cfg)
    embed.set_footer(text="Settings updated.")
    components = await components_builder(bot, guild_id, cfg)
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_UPDATE,
        embed=embed,
        components=components,
    )


# ---------------------------------------------------------------------------- #
#                                   Commands                                   #
# ---------------------------------------------------------------------------- #


def _prize_roles_embed(cfg: SubDayGuildConfig) -> hikari.Embed:
    """Build an embed showing current milestone role settings."""
    embed = hikari.Embed(
        title="Where I am Led — Milestone Roles",
        description=(
            "Use the dropdowns below to set milestone roles. "
            "Deselect to disable role assignment for that milestone."
        ),
        color=SOLARIZED_VIOLET,
    )
    for week in MILESTONE_WEEKS:
        role_name = getattr(cfg, f"role_{week}")
        embed.add_field(
            name=f"Week {week}",
            value=f"`{role_name}`" if role_name else "_None (no role granted)_",
            inline=True,
        )
    return embed


async def _prize_roles_components(
    bot: DragonpawBot,
    guild_id: hikari.Snowflakeish,
    cfg: SubDayGuildConfig,
) -> list[hikari.api.ComponentBuilder]:
    """Build the action rows for the prize-roles message with current values pre-selected."""
    roles = await bot.rest.fetch_roles(guild_id)
    role_map = {r.name: r.id for r in roles}

    rows: list[hikari.api.ComponentBuilder] = []
    for week in MILESTONE_WEEKS:
        role_name = getattr(cfg, f"role_{week}")
        rows.append(
            _DefaultsActionRow(
                bot.rest.build_message_action_row().add_select_menu(
                    hikari.ComponentType.ROLE_SELECT_MENU,
                    f"{SUBDAY_CFG_ROLE_PREFIX}role_{week}",
                    placeholder=f"Week {week} milestone role",
                    min_values=0,
                    max_values=1,
                ),
                [{"id": str(role_map[role_name]), "type": "role"}]
                if role_name and role_name in role_map
                else [],
            )
        )
    return rows


class SubDaySettings(
    lightbulb.SlashCommand,
    name="settings",
    description="Configure SubDay settings for this server (owner only)",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)
        if not await gc.require_owner(ctx):
            return
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        embed = _config_embed(cfg)
        components = await _config_components(gc.bot, ctx.guild_id, cfg)
        await ctx.respond(
            embed=embed,
            components=components,
            flags=hikari.MessageFlag.EPHEMERAL,
        )


class SubDayPrizeRoles(
    lightbulb.SlashCommand,
    name="prize-roles",
    description="Configure milestone roles for this server (owner only)",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)
        if not await gc.require_owner(ctx):
            return
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        embed = _prize_roles_embed(cfg)
        components = await _prize_roles_components(gc.bot, ctx.guild_id, cfg)
        await ctx.respond(
            embed=embed,
            components=components,
            flags=hikari.MessageFlag.EPHEMERAL,
        )


class SubDayPrizes(
    lightbulb.SlashCommand,
    name="prizes",
    description="Set milestone prize descriptions (owner only)",
):
    prize_13 = lightbulb.string("prize_13", "Prize for week 13 milestone", default=None)
    prize_26 = lightbulb.string("prize_26", "Prize for week 26 milestone", default=None)
    prize_39 = lightbulb.string("prize_39", "Prize for week 39 milestone", default=None)
    prize_52 = lightbulb.string(
        "prize_52", "Prize for week 52 graduation", default=None
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)
        if not await gc.require_owner(ctx):
            return
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        changed = False
        log = gc.logger
        for field in ("prize_13", "prize_26", "prize_39", "prize_52"):
            value = getattr(self, field, None)
            if value is not None:
                old_value = getattr(cfg, field)
                setattr(cfg, field, value)
                changed = True
                log.info(
                    "SubDay prize updated",
                    field=field,
                    old_value=old_value,
                    new_value=value,
                )

        if changed:
            await gc.fetch_guild()
            guild_state.guild_name = gc.name
            state.save(guild_state)

        prizes = cfg.milestone_prizes()
        prize_lines = [f"**Week {w}:** {p}" for w, p in sorted(prizes.items())]
        embed = hikari.Embed(
            title="Where I am Led — Milestone Prizes",
            description="\n".join(prize_lines),
            color=SOLARIZED_VIOLET,
        )
        if changed:
            embed.set_footer(text="Prizes updated.")

        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


def register(subgroup: lightbulb.SubGroup) -> None:
    subgroup.register(SubDaySettings)
    subgroup.register(SubDayPrizeRoles)
    subgroup.register(SubDayPrizes)
