# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot.plugins.channel_cleanup import state as cleanup_state
from dragonpaw_bot.utils import ChannelContext, GuildContext

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

loader = lightbulb.Loader()


async def _purge_channel(cc: ChannelContext, expiry_minutes: int) -> None:
    try:
        deleted = await cc.purge_old_messages(expiry_minutes)
        if deleted:
            logger.info(
                "Purged old messages",
                guild=cc.name,
                channel=cc.channel_name,
                count=deleted,
            )
    except Exception:
        logger.exception(
            "Cleanup cron error",
            guild=cc.name,
            channel=cc.channel_name,
        )


@loader.task(lightbulb.crontrigger("0 * * * *"))
async def hourly_cleanup(bot: hikari.GatewayBot) -> None:
    """Hourly task: purge old messages from configured channels (all channels run concurrently)."""
    assert isinstance(bot, DragonpawBot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.debug("Channel cleanup hourly run", guild_count=len(guilds))

    tasks = []
    for guild in guilds:
        gc = GuildContext.from_guild(bot, guild)
        for entry in cleanup_state.load(int(guild.id)).channels:
            cc = ChannelContext.from_entry(gc, entry)
            tasks.append(_purge_channel(cc, entry.expiry_minutes))
    if tasks:
        await asyncio.gather(*tasks)
