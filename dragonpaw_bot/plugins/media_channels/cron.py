from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import hikari  # noqa: TC002 — needed at runtime for DI annotation resolution
import lightbulb
import structlog

from dragonpaw_bot.context import ChannelContext, GuildContext
from dragonpaw_bot.plugins.media_channels import loader
from dragonpaw_bot.plugins.media_channels import state as media_state

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)


@loader.task(lightbulb.crontrigger("30 * * * *"))
async def hourly_media_cleanup(bot: hikari.GatewayBot) -> None:
    """Hourly task: purge old messages from media channels with expiry configured (all concurrent)."""
    bot = cast("DragonpawBot", bot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.debug("Media channel cleanup hourly run", guild_count=len(guilds))

    tasks = []
    for guild in guilds:
        gc = GuildContext.from_guild(bot, guild)
        for entry in media_state.load(int(guild.id)).channels:
            if entry.expiry_minutes is not None:
                cc = ChannelContext.from_entry(gc, entry)
                tasks.append(cc.run_cleanup(entry.expiry_minutes))
    if tasks:
        await asyncio.gather(*tasks)
