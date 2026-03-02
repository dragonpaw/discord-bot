from __future__ import annotations

import logging

import hikari
import lightbulb

from dragonpaw_bot import utils
from dragonpaw_bot.colors import SOLARIZED_VIOLET
from dragonpaw_bot.plugins.subday import commands, scheduler
from dragonpaw_bot.plugins.subday.commands import (
    HEADMISTRESS_ROLE,
    MILESTONE_ROLES,
    SUBMISSIVE_ROLE,
    TOTAL_WEEKS,
)

__all__ = ["MILESTONE_ROLES", "TOTAL_WEEKS"]

logger = logging.getLogger(__name__)

plugin = lightbulb.Plugin("SubDay")


def load(bot: lightbulb.BotApp) -> None:
    logger.info("Loading SubDay plugin")
    bot.add_plugin(plugin)


def unload(bot: lightbulb.BotApp) -> None:
    logger.info("Unloading SubDay plugin")
    bot.remove_plugin(plugin)


@plugin.command
@lightbulb.command("subday", "Where I am Led \u2014 52-week guided journal")
@lightbulb.implements(lightbulb.SlashCommandGroup)
async def subday_group(ctx: lightbulb.Context) -> None:
    lines = [
        "`/subday about` \u2014 Learn about the program",
        "`/subday status` \u2014 Check your progress",
    ]
    if ctx.member and utils.member_has_role(ctx.member, SUBMISSIVE_ROLE):
        lines.append("`/subday signup` \u2014 Sign up for the program")
    if ctx.member and utils.member_has_role(ctx.member, HEADMISTRESS_ROLE):
        lines.append("`/subday complete @user` \u2014 Mark a week complete")
        lines.append("`/subday list` \u2014 View all participants")
        lines.append("`/subday remove @user` \u2014 Remove a participant")
    if ctx.app.owner_ids and ctx.author.id in ctx.app.owner_ids:
        lines.append("`/subday setweek @user <week>` \u2014 Set week (owner)")

    embed = hikari.Embed(
        title="Where I am Led \u2014 Commands",
        description="\n".join(lines),
        color=SOLARIZED_VIOLET,
    )
    await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


commands.register(subday_group)


@plugin.listener(hikari.StartedEvent)
async def on_started(_event: hikari.StartedEvent) -> None:
    scheduler.start(plugin.bot)  # type: ignore[arg-type]


@plugin.listener(hikari.StoppedEvent)
async def on_stopped(_event: hikari.StoppedEvent) -> None:
    scheduler.stop()
