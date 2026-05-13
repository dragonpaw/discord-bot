from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import hikari  # noqa: TC002 — needed at runtime for DI annotation resolution
import lightbulb
import structlog

from dragonpaw_bot.context import ChannelContext, GuildContext
from dragonpaw_bot.plugins.media_channels import state as media_state

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)
loader = lightbulb.Loader()


async def _safe_run_cleanup(cc: ChannelContext, expiry_minutes: int) -> None:
    """Run cleanup for one channel, isolating failures so concurrent tasks aren't lost.

    `run_cleanup` already catches purge errors internally; this wrapper exists
    so anything that escapes (e.g., NotFoundError from check_perms when a
    channel has been deleted) doesn't vanish into asyncio.gather's result list.
    """
    try:
        await cc.run_cleanup(expiry_minutes)
    except Exception:
        logger.exception(
            "Unhandled cleanup error",
            guild=cc.name,
            channel=cc.channel_name,
        )


async def media_channels_hourly(bot: hikari.GatewayBot) -> None:
    """Hourly task: purge old messages from media channels with expiry configured (all concurrent)."""
    bot = cast("DragonpawBot", bot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.debug("Media channel cleanup hourly run", guild_count=len(guilds))

    tasks = []
    for guild in guilds:
        try:
            gc = GuildContext.from_guild(bot, guild)
            for entry in media_state.load(int(guild.id)).channels:
                if entry.expiry_minutes is not None:
                    cc = ChannelContext.from_entry(gc, entry)
                    tasks.append(_safe_run_cleanup(cc, entry.expiry_minutes))
        except Exception:
            logger.exception("Error building cleanup tasks for guild", guild=guild.name)
    if tasks:
        await asyncio.gather(*tasks)


@loader.task(lightbulb.crontrigger("30 * * * *"))
async def _media_channels_hourly_task(bot: hikari.GatewayBot) -> None:
    await media_channels_hourly(bot)
