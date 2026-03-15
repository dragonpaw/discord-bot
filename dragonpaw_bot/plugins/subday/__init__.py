from __future__ import annotations

from typing import TYPE_CHECKING

import lightbulb

from dragonpaw_bot.plugins.subday import commands
from dragonpaw_bot.plugins.subday import config as subday_config
from dragonpaw_bot.plugins.subday.constants import (
    MILESTONE_WEEKS,
    SUBDAY_CFG_ROLE_PREFIX,
    SUBDAY_CONFIG_PREFIX,
    SUBDAY_OWNER_REQUEST_PREFIX,
    SUBDAY_SIGNUP_ID,
    TOTAL_WEEKS,
)

if TYPE_CHECKING:
    from dragonpaw_bot.utils import InteractionHandler

__all__ = ["INTERACTION_HANDLERS", "MILESTONE_WEEKS", "TOTAL_WEEKS"]

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    SUBDAY_OWNER_REQUEST_PREFIX: commands.handle_owner_interaction,
    SUBDAY_SIGNUP_ID: commands.handle_signup_interaction,
    SUBDAY_CONFIG_PREFIX: subday_config.handle_config_interaction,
    SUBDAY_CFG_ROLE_PREFIX: subday_config.handle_config_interaction,
}

loader = lightbulb.Loader()

subday_group = lightbulb.Group("subday", "Where I am Led — 52-week guided journal")


@subday_group.register
class SubDayHelp(
    lightbulb.SlashCommand,
    name="help",
    description="Show available SubDay commands",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        await commands.help_handler(ctx)


commands.register(subday_group)
loader.command(subday_group)

from dragonpaw_bot.plugins.subday import cron as _cron  # noqa: E402, F401
