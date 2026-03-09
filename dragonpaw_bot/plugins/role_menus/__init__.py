# -*- coding: utf-8 -*-
from __future__ import annotations

import hikari
import lightbulb
import structlog

from dragonpaw_bot.plugins.role_menus.commands import (
    configure_guild,
    configure_role_menus,
    handle_role_menu_interaction,
    parse_role_config,
)
from dragonpaw_bot.plugins.role_menus.constants import ROLE_MENU_PREFIX
from dragonpaw_bot.utils import GuildContext, InteractionHandler

__all__ = [
    "INTERACTION_HANDLERS",
    "configure_guild",
    "configure_role_menus",
    "parse_role_config",
]

logger = structlog.get_logger(__name__)

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
            logger.error("Interaction without a guild")
            return

        await ctx.respond("Config loading now...", flags=hikari.MessageFlag.EPHEMERAL)

        gc = GuildContext.from_ctx(ctx)
        gc.logger.info("Setting up guild with file", url=self.url)
        errors = await configure_guild(gc=gc, url=self.url)

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
