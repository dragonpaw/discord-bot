# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot import utils
from dragonpaw_bot.plugins.channel_cleanup import state as cleanup_state
from dragonpaw_bot.plugins.channel_cleanup.models import CleanupChannelEntry

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

loader = lightbulb.Loader()


async def _purge_channel(bot: DragonpawBot, guild_name: str, entry: CleanupChannelEntry) -> None:
    try:
        deleted = await utils.purge_old_messages(
            bot, guild_name, entry.channel_name, entry.channel_id, entry.expiry_minutes
        )
        if deleted:
            logger.info(
                "Purged old messages",
                guild=guild_name,
                channel=entry.channel_name,
                count=deleted,
            )
    except Exception:
        logger.exception(
            "Cleanup cron error",
            guild=guild_name,
            channel=entry.channel_name,
        )


@loader.task(lightbulb.crontrigger("0 * * * *"))
async def hourly_cleanup(bot: hikari.GatewayBot) -> None:
    """Hourly task: purge old messages from configured channels (all channels run concurrently)."""
    assert isinstance(bot, DragonpawBot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.debug("Channel cleanup hourly run", guild_count=len(guilds))

    tasks = [
        _purge_channel(bot, guild.name, entry)
        for guild in guilds
        for entry in cleanup_state.load(int(guild.id)).channels
    ]
    if tasks:
        await asyncio.gather(*tasks)
