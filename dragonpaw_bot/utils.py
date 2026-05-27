from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING

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


# ---------------------------------------------------------------------------- #
#                           Discord utility functions                          #
# ---------------------------------------------------------------------------- #


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
