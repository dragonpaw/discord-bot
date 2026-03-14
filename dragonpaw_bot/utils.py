from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING

import hikari
import hikari.messages
import structlog
from emojis.db.db import EMOJI_DB

if TYPE_CHECKING:
    from dragonpaw_bot.context import GuildContext

InteractionHandler = Callable[[hikari.ComponentInteraction], Awaitable[None]]
ModalHandler = Callable[[hikari.ModalInteraction], Awaitable[None]]

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------- #
#                           Discord utility functions                          #
# ---------------------------------------------------------------------------- #


async def guild_channel_by_name(
    gc: GuildContext,
    name: str,
) -> hikari.GuildTextChannel | None:
    logger.debug("Finding channel", name=name)
    guild = await gc.fetch_guild()
    if isinstance(guild, hikari.Guild):
        channels: Sequence[hikari.GuildChannel] = list(guild.get_channels().values())
        if not channels:
            channels = await gc.bot.rest.fetch_guild_channels(guild=guild.id)
    else:
        channels = await gc.bot.rest.fetch_guild_channels(guild=guild.id)
    for channel in channels:
        if channel.name == name and isinstance(channel, hikari.GuildTextChannel):
            return channel
    return None


async def guild_emojis(
    gc: GuildContext,
) -> Mapping[str, hikari.KnownCustomEmoji | hikari.UnicodeEmoji]:
    emoji_map: dict[str, hikari.KnownCustomEmoji | hikari.UnicodeEmoji] = {}

    custom_emojis = await gc.bot.rest.fetch_guild_emojis(guild=gc.guild_id)
    for e in custom_emojis:
        emoji_map[e.name] = e
        logger.debug("Guild emoji", name=e.name, emoji=e)

    for u in EMOJI_DB:
        for alias in u.aliases:
            emoji_map[alias] = hikari.UnicodeEmoji.parse(u.emoji)

    return emoji_map


async def guild_roles(gc: GuildContext) -> Mapping[str, hikari.Role]:
    roles = await gc.bot.rest.fetch_roles(guild=gc.guild_id)
    return {r.name: r for r in roles}


async def guild_role_by_name(
    gc: GuildContext,
    name: str,
) -> hikari.Role | None:
    roles = await gc.bot.rest.fetch_roles(guild=gc.guild_id)
    for r in roles:
        if r.name == name:
            return r
    return None
