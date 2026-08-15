from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import hikari
import hikari.messages
import structlog
from emojis.db.db import EMOJI_DB

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot
    from dragonpaw_bot.context import GuildContext

InteractionHandler = Callable[[hikari.ComponentInteraction], Awaitable[None]]
ModalHandler = Callable[[hikari.ModalInteraction], Awaitable[None]]

logger = structlog.get_logger(__name__)

# The event loop keeps only weak references to tasks, so anything fire-and-forget
# must be anchored here until it completes or it can be garbage-collected mid-run.
_background_tasks: set[asyncio.Task[None]] = set()


def create_background_task(coro: Coroutine[None, None, None]) -> None:
    """Run a coroutine in the background, logging (not raising) any exception."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_finish_background_task)


def _finish_background_task(task: asyncio.Task[None]) -> None:
    _background_tasks.discard(task)
    if not task.cancelled() and (exc := task.exception()):
        logger.warning("Background task failed", task=task.get_name(), exc_info=exc)


# ---------------------------------------------------------------------------- #
#                           Discord utility functions                          #
# ---------------------------------------------------------------------------- #

_URL_RE = re.compile(r"https?://", re.IGNORECASE)


def _msg_has_media(
    attachments: Sequence[hikari.Attachment],
    content: str | None,
    stickers: Sequence[hikari.PartialSticker],
) -> bool:
    return (
        bool(attachments) or _URL_RE.search(content or "") is not None or bool(stickers)
    )


class DefaultsActionRow(hikari.api.ComponentBuilder):
    """Wraps a MessageActionRowBuilder to inject default_values into the payload.

    Hikari's select menu builders don't emit ``default_values`` for
    auto-populated selects (role/channel).  Discord's API *does* support
    the field, so we patch it into the built dict.
    """

    def __init__(
        self,
        inner: hikari.api.ComponentBuilder,
        defaults: list[dict[str, str]],
    ) -> None:
        self._inner = inner
        self._defaults = defaults

    @property
    def type(self) -> int | hikari.ComponentType:
        return self._inner.type

    @property
    def id(self) -> hikari.UndefinedOr[int]:
        return self._inner.id

    def build(self) -> Any:
        payload, resources = self._inner.build()
        if self._defaults and payload.get("components"):
            payload["components"][0]["default_values"] = self._defaults
        return payload, resources


def message_has_media(message: hikari.Message) -> bool:
    """Return True if this message (or any forwarded snapshot) contains media
    (attachments, URLs, or stickers)."""
    if _msg_has_media(message.attachments, message.content, message.stickers):
        return True
    # Forwarded messages carry their media inside message_snapshots, not on the
    # message itself — check each snapshot with the same rules.
    return any(
        _msg_has_media(s.attachments, s.content, s.stickers)
        for s in message.message_snapshots
    )


async def guild_members(
    bot: DragonpawBot, guild_id: hikari.Snowflakeish
) -> list[hikari.Member]:
    """All members of a guild, from cache when populated, else a REST fallback.

    Relies on the GUILD_MEMBERS intent and hikari's startup member chunking (both
    on by default), so this normally avoids a paginated REST call. The fallback
    only covers a *fully* empty cache (cold start before any chunk arrives) — a
    cache that is mid-chunk can return a non-empty but **incomplete** list. Never
    treat a member's absence from this list as authoritative "left the guild";
    confirm with `guild_member(...) is None` (a real REST 404) before acting
    destructively on a presumed departure.
    """
    members = list(bot.cache.get_members_view_for_guild(guild_id).values())
    if not members:
        members = [m async for m in bot.rest.fetch_members(guild_id)]
    return members


async def guild_member(
    bot: DragonpawBot,
    guild_id: hikari.Snowflakeish,
    user_id: hikari.Snowflakeish,
) -> hikari.Member | None:
    """A single guild member, from cache when present, else REST.

    Returns None if the member isn't in the guild (REST 404), letting callers
    tell a departed member apart from a cache miss.
    """
    member = bot.cache.get_member(guild_id, user_id)
    if member is not None:
        return member
    try:
        return await bot.rest.fetch_member(guild_id, user_id)
    except hikari.NotFoundError:
        return None


async def guild_channel_by_name(
    gc: GuildContext,
    name: str,
) -> hikari.GuildTextChannel | None:
    logger.debug("Finding channel", name=name)
    channels = gc.bot.cache.get_guild_channels_view_for_guild(gc.guild_id)
    for channel in channels.values():
        if channel.name == name and isinstance(channel, hikari.GuildTextChannel):
            return channel
    return None


async def guild_emojis(
    gc: GuildContext,
) -> Mapping[str, hikari.KnownCustomEmoji | hikari.UnicodeEmoji]:
    emoji_map: dict[str, hikari.KnownCustomEmoji | hikari.UnicodeEmoji] = {}

    for e in gc.bot.cache.get_emojis_view_for_guild(gc.guild_id).values():
        emoji_map[e.name] = e
        logger.debug("Guild emoji", name=e.name, emoji=e)

    for u in EMOJI_DB:
        for alias in u.aliases:
            emoji_map[alias] = hikari.UnicodeEmoji.parse(u.emoji)

    return emoji_map


async def guild_roles(gc: GuildContext) -> Mapping[str, hikari.Role]:
    return {
        r.name: r for r in gc.bot.cache.get_roles_view_for_guild(gc.guild_id).values()
    }


async def guild_role_by_name(
    gc: GuildContext,
    name: str,
) -> hikari.Role | None:
    for r in gc.bot.cache.get_roles_view_for_guild(gc.guild_id).values():
        if r.name == name:
            return r
    return None
