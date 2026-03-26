from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import hikari  # noqa: TC002 — needed at runtime for DI annotation resolution
import lightbulb
import structlog

from dragonpaw_bot.context import CHANNEL_CLEANUP_PERMS, ChannelContext, GuildContext
from dragonpaw_bot.plugins.channel_cleanup import loader
from dragonpaw_bot.plugins.channel_cleanup import state as cleanup_state

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)


async def _purge_channel(cc: ChannelContext, expiry_minutes: int) -> None:
    missing = await cc.check_perms(CHANNEL_CLEANUP_PERMS)
    if missing:
        cc.logger.warning(
            "Missing permissions for cleanup, skipping",
            channel=cc.channel_name,
            missing=missing,
        )
        await cc.log(
            f"⚠️ I'm missing **{', '.join(missing)}** in **#{cc.channel_name}** "
            f"and can't run cleanup. Please fix the channel permissions."
        )
        return
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
        await cc.log(
            f"🐛 I hit an unexpected error cleaning **#{cc.channel_name}** — check the bot logs."
        )


@loader.task(lightbulb.crontrigger("0 * * * *"))
async def hourly_cleanup(bot: hikari.GatewayBot) -> None:
    """Hourly task: purge old messages from configured channels (all channels run concurrently)."""
    bot = cast("DragonpawBot", bot)
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
