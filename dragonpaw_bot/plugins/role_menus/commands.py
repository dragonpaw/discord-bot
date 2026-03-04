# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

import hikari

from dragonpaw_bot import utils
from dragonpaw_bot.colors import rainbow
from dragonpaw_bot.plugins.role_menus import state
from dragonpaw_bot.plugins.role_menus.constants import ROLE_MENU_PREFIX
from dragonpaw_bot.plugins.role_menus.models import (
    RoleMenuConfig,
    RoleMenuGuildState,
    RoleMenuState,
    RolesConfig,
)

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = logging.getLogger(__name__)

SINGLE_ROLE_NOTE = "You can only pick one option from this menu."


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


def build_menu_select(
    menu_index: int,
    menu: RoleMenuConfig,
    valid_options: list[tuple[str, str, str | None]],
    emoji_map: Mapping[str, hikari.KnownCustomEmoji | hikari.UnicodeEmoji],
) -> hikari.api.TextSelectMenuBuilder:
    """Build a text select menu component.

    valid_options is a list of (role_name, description, emoji_name|None).
    """
    custom_id = f"{ROLE_MENU_PREFIX}{menu_index}"

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
    bot: DragonpawBot,
    guild: hikari.Guild,
    config: RolesConfig,
    role_map: Mapping[str, hikari.Role],
) -> list[str]:
    """Setup the role channel for the guild.

    This wipes out all old role messages and sends new ones with select menus.
    """
    errors: list[str] = []

    channel = await utils.guild_channel_by_name(
        bot=bot, guild=guild, name=config.channel
    )
    if not channel:
        errors.append(f"Role channel '{config.channel}' doesn't seem to exist.")
        return errors

    if not config.menu:
        errors.append("Role channel is set, but no role menus seem to exist.")
        return errors

    emoji_map = await utils.guild_emojis(bot=bot, guild=guild)

    logger.debug("Trying to delete old role menus...")
    await utils.delete_my_messages(
        bot=bot, guild_name=guild.name, channel_id=channel.id
    )

    guild_state = RoleMenuGuildState(
        guild_id=int(guild.id),
        guild_name=guild.name,
        role_channel_id=int(channel.id),
        role_names={int(r.id): r.name for r in role_map.values()},
    )

    colors = rainbow(len(config.menu))
    for menu_index, menu in enumerate(config.menu):
        logger.info("G=%r Adding the menu: %s", guild.name, menu.name)
        embed = build_menu_embed(menu, colors[menu_index])

        valid_options: list[tuple[str, str, str | None]] = []
        for o in menu.options:
            if o.role not in role_map:
                logger.error(
                    "G=%r Menu=%r: Role %r doesn't exist.",
                    guild.name,
                    menu.name,
                    o.role,
                )
                errors.append(f"Role '{o.role}' doesn't seem to exist.")
                continue
            # Emoji is optional — warn if specified but missing in guild
            if o.emoji and o.emoji not in emoji_map:
                logger.warning(
                    "G=%r Menu=%r: Emoji %r doesn't exist, skipping decoration.",
                    guild.name,
                    menu.name,
                    o.emoji,
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
            embed.add_field(
                name=o.role,
                value=f"{o.description}\n_ _\n",
                inline=False,
            )

        if not valid_options:
            logger.warning(
                "G=%r Menu=%r: No valid options; menu posted with no select.",
                guild.name,
                menu.name,
            )
            await channel.send(embed=embed)
            continue

        select = build_menu_select(menu_index, menu, valid_options, emoji_map)
        row = hikari.impl.MessageActionRowBuilder().add_component(select)
        message = await channel.send(embed=embed, component=row)

        menu_state = RoleMenuState(
            menu_index=menu_index,
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
    """Parse menu index from custom_id and find the matching menu state."""
    try:
        menu_index = int(custom_id.removeprefix(ROLE_MENU_PREFIX))
    except ValueError:
        logger.error("Invalid role menu custom_id: %r", custom_id)
        return None

    for m in guild_state.menus:
        if m.menu_index == menu_index:
            return m

    logger.error(
        "No menu state for index %d in guild %d", menu_index, guild_state.guild_id
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
                await utils.log_to_guild(
                    bot,
                    guild_snowflake,
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
                await utils.log_to_guild(
                    bot,
                    guild_snowflake,
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
        parts.append(f"Failed (permission error): **{', '.join(failed)}**")
    return ". ".join(parts) if parts else "No role changes."


async def handle_role_menu_interaction(
    interaction: hikari.ComponentInteraction,
) -> None:
    """Handle a role menu select interaction."""
    if not interaction.guild_id or not interaction.member:
        logger.error(
            "Role menu interaction missing guild_id or member: custom_id=%r user=%r",
            interaction.custom_id,
            interaction.user.username,
        )
        return

    guild_id = int(interaction.guild_id)

    guild_state = state.load(guild_id)
    if not guild_state.menus:
        logger.warning("G=%d: Role menu interaction but no menus in state", guild_id)
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="This role menu is outdated. Please ask an admin to re-run `/config`.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    menu_state = _find_menu_state(guild_state, interaction.custom_id)
    if not menu_state:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="This role menu is no longer recognized. It may need to be reconfigured.",
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
        logger.warning(
            "G=%r U=%r: Role menu interaction expired before response",
            guild_state.guild_name,
            interaction.user.username,
        )
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
            "G=%r U=%r: Failed to send role change summary for menu %r",
            guild_state.guild_name,
            interaction.user.username,
            menu_state.menu_name,
        )

    if added or removed:
        logger.info(
            "G=%r U=%r: Role menu %r — added=%r removed=%r",
            guild_state.guild_name,
            interaction.user.username,
            menu_state.menu_name,
            added or None,
            removed or None,
        )
