# -*- coding: utf-8 -*-
from __future__ import annotations

import logging

import hikari
import lightbulb

from dragonpaw_bot.plugins.subday import commands, scheduler
from dragonpaw_bot.plugins.subday.commands import MILESTONE_ROLES, TOTAL_WEEKS

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
@lightbulb.command("subday", "Where I am Led — 52-week guided journal")
@lightbulb.implements(lightbulb.SlashCommandGroup)
async def subday_group(ctx: lightbulb.Context) -> None:
    await commands.help_handler(ctx)


commands.register(subday_group)


@plugin.listener(event=hikari.InteractionCreateEvent)
async def on_interaction(event: hikari.InteractionCreateEvent) -> None:
    if not isinstance(event.interaction, hikari.ComponentInteraction):
        return
    if not event.interaction.custom_id.startswith(commands.SUBDAY_CONFIG_PREFIX):
        return
    await commands.handle_config_interaction(event.interaction)


@plugin.listener(hikari.StartedEvent)
async def on_started(_event: hikari.StartedEvent) -> None:
    scheduler.start(plugin.bot)  # type: ignore[arg-type]


@plugin.listener(hikari.StoppedEvent)
async def on_stopped(_event: hikari.StoppedEvent) -> None:
    scheduler.stop()
