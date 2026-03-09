# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import re
import tomllib
from collections.abc import Mapping
from typing import TYPE_CHECKING

import hikari
import pydantic
import structlog

from dragonpaw_bot import http, structs, utils
from dragonpaw_bot.colors import rainbow
from dragonpaw_bot.plugins.role_menus import state
from dragonpaw_bot.plugins.role_menus.constants import ROLE_MENU_PREFIX
from dragonpaw_bot.plugins.role_menus.models import (
    RoleMenuConfig,
    RoleMenuGuildState,
    RoleMenuState,
    RolesConfig,
)
from dragonpaw_bot.utils import ChannelContext, GuildContext

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

SINGLE_ROLE_NOTE = "You can only pick one option from this menu."


def parse_role_config(text: str) -> RolesConfig:
    """Parse a TOML string in the flat role menu format into a RolesConfig."""
    data = tomllib.loads(text)
    return RolesConfig.model_validate(data)


def build_menu_embed(menu: RoleMenuConfig, color: tuple[int, int, int]) -> hikari.Embed:
    embed = hikari.Embed(color=hikari.Color.from_rgb(*color))
    if menu.single:
        embed.title = menu.name + " (Pick 1)"
        if menu.description:
            embed.description = menu.description + "\n_ _\n" + SINGLE_ROLE_NOTE
        else:
            embed.description = SINGLE_ROLE_NOTE
    else:
        embed.title = menu.name
        embed.description = menu.description
    return embed


def _slugify(name: str) -> str:
    """Convert a menu name to a URL-safe slug for use in custom_ids."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def build_menu_select(
    menu_slug: str,
    menu: RoleMenuConfig,
    valid_options: list[tuple[str, str, str | None]],
    emoji_map: Mapping[str, hikari.KnownCustomEmoji | hikari.UnicodeEmoji],
) -> hikari.api.TextSelectMenuBuilder:
    """Build a text select menu component.

    valid_options is a list of (role_name, description, emoji_name|None).
    """
    custom_id = f"{ROLE_MENU_PREFIX}{menu_slug}"

    if menu.single:
        max_values = 1
    else:
        max_values = len(valid_options)

    select = hikari.impl.TextSelectMenuBuilder(
        custom_id=custom_id,
        placeholder=f"Select from {menu.name}...",
        min_values=0,
        max_values=max_values,
    )

    for role_name, description, emoji_name in valid_options:
        emoji: hikari.Emoji | hikari.UndefinedType = hikari.UNDEFINED
        if emoji_name and emoji_name in emoji_map:
            emoji = emoji_map[emoji_name]
        _MAX_DESC = 100
        desc = (
            description[: _MAX_DESC - 1] + "…"
            if len(description) > _MAX_DESC
            else description
        )
        select.add_option(role_name, role_name, description=desc, emoji=emoji)

    return select


async def configure_role_menus(
    gc: GuildContext,
    config: RolesConfig,
    role_map: Mapping[str, hikari.Role],
) -> list[str]:
    """Setup the role channel for the guild.

    This wipes out all old role messages and sends new ones with select menus.
    """
    log = gc.logger
    errors: list[str] = []

    channel = await utils.guild_channel_by_name(gc, config.channel)
    if not channel:
        errors.append(f"Role channel '{config.channel}' doesn't seem to exist.")
        return errors

    if not config.menu:
        errors.append("Role channel is set, but no role menus seem to exist.")
        return errors

    emoji_map = await utils.guild_emojis(gc)

    log.debug("Trying to delete old role menus...")
    cc = ChannelContext.from_entry(
        gc,
        type(
            "_Entry",
            (),
            {"channel_id": int(channel.id), "channel_name": channel.name or ""},
        )(),
    )
    await cc.delete_my_messages()

    guild_state = RoleMenuGuildState(
        guild_id=int(gc.guild_id),
        guild_name=gc.name,
        role_channel_id=int(channel.id),
        role_names={int(r.id): r.name for r in role_map.values()},
    )

    colors = rainbow(len(config.menu))
    for menu_index, menu in enumerate(config.menu):
        menu_slug = _slugify(menu.name)
        log.info("Adding the menu", menu=menu.name)
        embed = build_menu_embed(menu, colors[menu_index])

        valid_options: list[tuple[str, str, str | None]] = []
        for o in menu.options:
            if o.role not in role_map:
                log.warning(
                    "Role doesn't exist",
                    menu=menu.name,
                    role=o.role,
                )
                errors.append(f"Role '{o.role}' doesn't seem to exist.")
                continue
            # Emoji is optional — warn if specified but missing in guild
            if o.emoji and o.emoji not in emoji_map:
                log.warning(
                    "Emoji doesn't exist, skipping decoration",
                    menu=menu.name,
                    emoji=o.emoji,
                )
                errors.append(
                    f"Menu '{menu.name}': Emoji '{o.emoji}' not found, skipped."
                )
            _MAX_DESC = 100
            if len(o.description) > _MAX_DESC:
                errors.append(
                    f"Menu '{menu.name}', role '{o.role}': "
                    f"Description truncated from {len(o.description)} to {_MAX_DESC} chars."
                )
            valid_options.append((o.role, o.description, o.emoji))
            if o.emoji and o.emoji in emoji_map:
                field_name = f"{emoji_map[o.emoji].mention} {o.role}"
            else:
                field_name = o.role
            embed.add_field(
                name=field_name,
                value=f"{o.description}\n_ _\n",
                inline=False,
            )

        if not valid_options:
            log.warning(
                "No valid options; menu posted with no select",
                menu=menu.name,
            )
            await channel.send(embed=embed)
            continue

        select = build_menu_select(menu_slug, menu, valid_options, emoji_map)
        row = hikari.impl.MessageActionRowBuilder().add_component(select)
        message = await channel.send(embed=embed, component=row)

        menu_state = RoleMenuState(
            menu_slug=menu_slug,
            menu_name=menu.name,
            message_id=int(message.id),
            single=menu.single,
            option_role_ids={
                role_name: int(role_map[role_name].id)
                for role_name, _, _ in valid_options
            },
        )
        guild_state.menus.append(menu_state)

    state.save(guild_state)
    return errors


def _find_menu_state(
    guild_state: RoleMenuGuildState, custom_id: str
) -> RoleMenuState | None:
    """Parse menu slug from custom_id and find the matching menu state."""
    menu_slug = custom_id.removeprefix(ROLE_MENU_PREFIX)
    if not menu_slug:
        logger.error("Empty role menu slug in custom_id", custom_id=custom_id)
        return None

    for m in guild_state.menus:
        if m.menu_slug == menu_slug:
            return m

    logger.error(
        "No menu state for slug in guild",
        menu_slug=menu_slug,
        guild_id=guild_state.guild_id,
    )
    return None


async def _apply_role_changes(
    interaction: hikari.ComponentInteraction,
    guild_state: RoleMenuGuildState,
    menu_state: RoleMenuState,
    selected_roles: set[str],
    member_role_ids: set[hikari.Snowflake],
) -> tuple[list[str], list[str], list[str]]:
    """Add/remove roles based on the diff. Returns (added, removed, failed) name lists."""
    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    gc = GuildContext.from_interaction(interaction)
    guild_snowflake = hikari.Snowflake(guild_state.guild_id)
    added: list[str] = []
    removed: list[str] = []
    failed: list[str] = []

    for role_name, role_id in menu_state.option_role_ids.items():
        snowflake_id = hikari.Snowflake(role_id)
        has_role = snowflake_id in member_role_ids
        wants_role = role_name in selected_roles

        if wants_role and not has_role:
            try:
                await bot.rest.add_role_to_member(
                    guild=guild_snowflake,
                    user=interaction.user.id,
                    role=snowflake_id,
                    reason="Role menu selection",
                )
                added.append(role_name)
            except hikari.ForbiddenError:
                display = guild_state.role_names.get(role_id, role_name)
                failed.append(display)
                await gc.log(
                    f"🤯 Unable to add role: **{display}**, "
                    "please check my permissions relative to that role.",
                )
        elif has_role and not wants_role:
            try:
                await bot.rest.remove_role_from_member(
                    guild=guild_snowflake,
                    user=interaction.user.id,
                    role=snowflake_id,
                    reason="Role menu selection",
                )
                removed.append(role_name)
            except hikari.ForbiddenError:
                display = guild_state.role_names.get(role_id, role_name)
                failed.append(display)
                await gc.log(
                    f"🤯 Unable to remove role: **{display}**, "
                    "please check my permissions relative to that role.",
                )

    return added, removed, failed


def _build_summary(added: list[str], removed: list[str], failed: list[str]) -> str:
    """Build a human-readable summary of role changes."""
    parts: list[str] = []
    if added:
        parts.append(f"Added: **{', '.join(added)}**")
    if removed:
        parts.append(f"Removed: **{', '.join(removed)}**")
    if failed:
        parts.append(
            f"Couldn't change: **{', '.join(failed)}** (permission error) — poke an admin! 🐾"
        )
    return (
        ". ".join(parts)
        if parts
        else "No changes this time! Your roles are just the way you left them 🐾"
    )


async def handle_role_menu_interaction(
    interaction: hikari.ComponentInteraction,
) -> None:
    """Handle a role menu select interaction."""
    if not interaction.guild_id or not interaction.member:
        logger.error(
            "Role menu interaction missing guild_id or member",
            custom_id=interaction.custom_id,
        )
        return

    guild_id = int(interaction.guild_id)

    guild_state = state.load(guild_id)
    if not guild_state.menus:
        logger.warning("Role menu interaction but no menus in state")
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="*confused dragon noises* 🐉 This menu seems outdated! Could you ask an admin to re-run `/config`?",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    menu_state = _find_menu_state(guild_state, interaction.custom_id)
    if not menu_state:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="*tilts head* 🐉 I don't recognize this menu anymore. It might need an admin to reconfigure it!",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    # Respond with deferred ephemeral message (keeps the select menu intact)
    try:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.DEFERRED_MESSAGE_CREATE,
            flags=hikari.MessageFlag.EPHEMERAL,
        )
    except hikari.NotFoundError:
        logger.warning("Role menu interaction expired before response")
        return

    selected_roles = set(interaction.values) if interaction.values else set()
    member_role_ids = set(interaction.member.role_ids)

    added, removed, failed = await _apply_role_changes(
        interaction, guild_state, menu_state, selected_roles, member_role_ids
    )

    try:
        await interaction.edit_initial_response(
            content=_build_summary(added, removed, failed)
        )
    except hikari.HTTPError:
        logger.warning(
            "Failed to send role change summary",
            menu=menu_state.menu_name,
        )

    if added or removed:
        logger.info(
            "Role menu changes applied",
            menu=menu_state.menu_name,
            added=added or None,
            removed=removed or None,
        )


async def configure_guild(gc: GuildContext, url: str) -> list[str]:
    """Load a role menu config for a guild and set up role menus.

    Returns a list of warning/error messages for the caller to display.
    """
    log = gc.logger
    all_errors: list[str] = []

    if url.startswith("https://gist.github.com"):
        config_text = await http.get_gist(url)
    else:
        config_text = await http.get_text(url)
    try:
        config = parse_role_config(config_text)
    except tomllib.TOMLDecodeError as e:
        log.error("Error parsing TOML file", error=str(e))
        await gc.log(f"🤯 **Config error:** {e}")
        return [f"Config error: {e}"]
    except pydantic.ValidationError as e:
        log.error("Config validation error", error=str(e))
        await gc.log(f"🤯 **Config validation error:** {e}")
        return [f"Config validation error: {e}"]

    role_map = await utils.guild_roles(gc)

    old_state = gc.bot.state(gc.guild_id)
    guild_state = structs.GuildState(
        id=gc.guild_id,
        name=gc.name,
        config_url=url,
        config_last=datetime.datetime.now(),
        log_channel_id=old_state.log_channel_id if old_state else None,
    )

    errors = await configure_role_menus(
        gc=gc,
        config=config,
        role_map=role_map,
    )
    for err in errors:
        log.warning("Error setting up role menus", error=err)
        await gc.log(f"🤯 **Role menu error:** {err}")
    all_errors.extend(errors)

    gc.bot.state_update(guild_state)
    log.info("Configured guild.")
    return all_errors
