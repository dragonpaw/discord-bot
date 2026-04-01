"""Media channels plugin: enforces media-only policy in configured channels."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.media_channels import state as media_state

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
    bot: DragonpawBot,
    channel_id: hikari.Snowflake,
    message_id: hikari.Snowflake,
    delay: float,
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
    entry = next(
        (c for c in guild_st.channels if c.channel_id == event.channel_id), None
    )
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

    bot_st = bot.state(event.guild_id)
    redirect_id = entry.redirect_channel_id or (
        bot_st.general_channel_id if bot_st else None
    )
    redirect_hint = (
        f" Why not share your thoughts in <#{redirect_id}>? 🐾" if redirect_id else ""
    )
    notice_text = (
        f"*chomps happily* 🐉 Mmm, snacks! <@{msg.author.id}>, "
        f"this channel is for images, links, and files only — "
        f"so I had to nom that message right up.{redirect_hint}"
    )

    try:
        notice = await bot.rest.create_message(
            channel=event.channel_id, content=notice_text
        )
        task = asyncio.create_task(
            _delete_after(bot, event.channel_id, notice.id, delay=15.0)
        )
        task.add_done_callback(
            lambda t: (
                logger.warning(
                    "Unexpected error deleting notice message", error=str(t.exception())
                )
                if not t.cancelled() and t.exception() is not None
                else None
            )
        )
    except hikari.HTTPError as exc:
        logger.warning(
            "Cannot send notice in media channel",
            guild=guild_st.guild_name,
            channel=entry.channel_name,
            error=str(exc),
        )

    gc = GuildContext.from_guild(
        bot,
        bot.cache.get_guild(event.guild_id)
        or await bot.rest.fetch_guild(event.guild_id),
    )
    await gc.log(
        f"🐉 Nommed text-only post by {msg.author.mention} in <#{event.channel_id}>",
    )


from dragonpaw_bot.plugins.media_channels import cron as _cron  # noqa: E402, F401
