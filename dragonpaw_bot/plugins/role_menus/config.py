# -*- coding: utf-8 -*-
"""Slash commands: /config roles setup"""

from __future__ import annotations

import hikari
import lightbulb
import structlog

from dragonpaw_bot.plugins.role_menus.commands import configure_guild
from dragonpaw_bot.utils import GuildContext

logger = structlog.get_logger(__name__)


class RolesSetup(
    lightbulb.SlashCommand,
    name="setup",
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


def register(subgroup: lightbulb.SubGroup) -> None:
    subgroup.register(RolesSetup)
