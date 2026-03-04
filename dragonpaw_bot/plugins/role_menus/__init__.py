# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import hikari
import lightbulb

from dragonpaw_bot.plugins.role_menus.commands import (
    configure_guild,
    configure_role_menus,
    handle_role_menu_interaction,
    parse_role_config,
)
from dragonpaw_bot.plugins.role_menus.constants import ROLE_MENU_PREFIX
from dragonpaw_bot.utils import InteractionHandler

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

__all__ = [
    "INTERACTION_HANDLERS",
    "configure_guild",
    "configure_role_menus",
    "parse_role_config",
]

logger = logging.getLogger(__name__)

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    ROLE_MENU_PREFIX: handle_role_menu_interaction,
}

loader = lightbulb.Loader()

roles_group = lightbulb.Group("roles", "Role menu management")
loader.command(roles_group)


@roles_group.register
class RolesConfigCommand(
    lightbulb.SlashCommand,
    name="config",
    description="Configure role menus via a URL to a TOML file.",
    hooks=[lightbulb.prefab.has_permissions(hikari.Permissions.MANAGE_ROLES)],
):
    url = lightbulb.string("url", "Link to the config you wish to use")

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            logger.error("Interaction without a guild?!: %r", ctx)
            return

        await ctx.respond("Config loading now...", flags=hikari.MessageFlag.EPHEMERAL)

        bot: DragonpawBot = ctx.client.app  # type: ignore[assignment]
        guild = await bot.rest.fetch_guild(guild=ctx.guild_id)
        logger.info("G=%r Setting up guild with file %r", guild.name, self.url)
        errors = await configure_guild(bot=bot, guild=guild, url=self.url)

        if errors:
            error_lines = "\n".join(f"- {e}" for e in errors)
            await ctx.respond(
                f"⚠️ **Config loaded with warnings:**\n{error_lines}",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
        else:
            await ctx.respond(
                "✅ Config loaded successfully.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
