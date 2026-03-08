# -*- coding: utf-8 -*-
"""Media channels plugin: enforces media-only policy in configured channels."""
from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot import utils
from dragonpaw_bot.plugins.media_channels import state as media_state
from dragonpaw_bot.plugins.media_channels.models import MediaChannelEntry

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

loader = lightbulb.Loader()

_URL_RE = re.compile(r"https?://", re.IGNORECASE)


def _has_media(message: hikari.Message) -> bool:
    """Return True if this message contains an attachment, URL, or sticker."""
    return (
        bool(message.attachments)
        or _URL_RE.search(message.content or "") is not None
        or bool(message.stickers)
    )


async def _delete_after(
    bot: DragonpawBot, channel_id: hikari.Snowflake, message_id: hikari.Snowflake, delay: float
) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.rest.delete_message(channel=channel_id, message=message_id)
    except hikari.NotFoundError:
        pass  # Already deleted — expected race condition
    except hikari.ForbiddenError:
        logger.warning(
            "Cannot delete notice message, missing Manage Messages permission",
            channel_id=int(channel_id),
        )


@loader.listener(hikari.GuildMessageCreateEvent)
async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
    """Delete text-only posts from media-only channels and post a brief dragon notice."""
    if event.message.author.is_bot:
        return

    bot: DragonpawBot = event.app  # type: ignore[assignment]
    msg = event.message

    guild_st = media_state.load(int(event.guild_id))
    entry = next((c for c in guild_st.channels if c.channel_id == event.channel_id), None)
    if entry is None:
        return

    if _has_media(msg):
        return

    try:
        await bot.rest.delete_message(channel=event.channel_id, message=msg.id)
    except hikari.NotFoundError:
        return  # User deleted their own message — enforcement is moot
    except hikari.ForbiddenError:
        logger.warning(
            "Cannot delete message, missing Manage Messages permission",
            guild=guild_st.guild_name,
            channel=entry.channel_name,
        )
        return

    redirect_hint = (
        f" Why not share your thoughts in <#{entry.redirect_channel_id}>? 🐾"
        if entry.redirect_channel_id
        else ""
    )
    notice_text = (
        f"*chomps happily* 🐉 Mmm, snacks! <@{msg.author.id}>, "
        f"this channel is for images, links, and files only — "
        f"so I had to nom that message right up.{redirect_hint}"
    )

    try:
        notice = await bot.rest.create_message(channel=event.channel_id, content=notice_text)
        task = asyncio.create_task(
            _delete_after(bot, event.channel_id, notice.id, delay=15.0)
        )
        task.add_done_callback(
            lambda t: logger.warning(
                "Unexpected error deleting notice message", error=str(t.exception())
            )
            if not t.cancelled() and t.exception() is not None
            else None
        )
    except hikari.HTTPError as exc:
        logger.warning(
            "Cannot send notice in media channel",
            guild=guild_st.guild_name,
            channel=entry.channel_name,
            error=str(exc),
        )

    await utils.log_to_guild(
        bot,
        event.guild_id,
        f"🐉 Nommed text-only post by {msg.author.username} in <#{event.channel_id}>",
    )


async def _purge_media_channel(
    bot: DragonpawBot, guild_name: str, entry: MediaChannelEntry
) -> None:
    if entry.expiry_minutes is None:
        return
    try:
        deleted = await utils.purge_old_messages(
            bot, guild_name, entry.channel_name, entry.channel_id, entry.expiry_minutes
        )
        if deleted:
            logger.info(
                "Purged old messages from media channel",
                guild=guild_name,
                channel=entry.channel_name,
                count=deleted,
            )
    except Exception:
        logger.exception(
            "Media channel cleanup cron error",
            guild=guild_name,
            channel=entry.channel_name,
        )


@loader.task(lightbulb.crontrigger("30 * * * *"))
async def hourly_media_cleanup(bot: hikari.GatewayBot) -> None:
    """Hourly task: purge old messages from media channels with expiry configured (all concurrent)."""
    assert isinstance(bot, DragonpawBot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.debug("Media channel cleanup hourly run", guild_count=len(guilds))

    tasks = [
        _purge_media_channel(bot, guild.name, entry)
        for guild in guilds
        for entry in media_state.load(int(guild.id)).channels
        if entry.expiry_minutes is not None
    ]
    if tasks:
        await asyncio.gather(*tasks)
