from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Mapping, Optional, Sequence, Union

import hikari
import hikari.messages
from emojis.db.db import EMOJI_DB

from dragonpaw_bot.colors import SOLARIZED_RED

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------- #
#                           Discord utility functions                          #
# ---------------------------------------------------------------------------- #


async def delete_my_messages(
    bot: DragonpawBot, guild_name: str, channel_id: hikari.Snowflake
):
    logger.debug("Checking for old messages in channel: %r", channel_id)
    assert bot.user_id
    async for message in bot.rest.fetch_messages(channel=channel_id):
        if message.author.id == bot.user_id:
            logger.debug("G:%r: Deleting my message: %r", guild_name, message)
            await message.delete()


async def guild_channel_by_name(
    bot: DragonpawBot,
    guild: hikari.Guild | hikari.Snowflake,
    name: str,
) -> Optional[hikari.GuildTextChannel]:
    logger.debug("Finding channel: %s", name)
    if isinstance(guild, hikari.Guild):
        channels: Sequence[hikari.GuildChannel] = list(guild.get_channels().values())
        if not channels:
            channels = await bot.rest.fetch_guild_channels(guild=guild.id)
    else:
        channels = await bot.rest.fetch_guild_channels(guild=guild)
    for channel in channels:
        if channel.name == name and isinstance(channel, hikari.GuildTextChannel):
            return channel
    return None


async def guild_emojis(
    bot: DragonpawBot, guild: hikari.Guild
) -> Mapping[str, Union[hikari.KnownCustomEmoji, hikari.UnicodeEmoji]]:
    emoji_map: dict[str, Union[hikari.KnownCustomEmoji, hikari.UnicodeEmoji]] = {}

    # Load the custom emojis from the guild
    custom_emojis = await bot.rest.fetch_guild_emojis(guild=guild.id)
    for e in custom_emojis:
        emoji_map[e.name] = e
        logger.debug("Guild emoji: %s:%r", e.name, e)

    # Shove the Global Emojis in there as well
    for u in EMOJI_DB:
        for alias in u.aliases:
            emoji_map[alias] = hikari.UnicodeEmoji.parse(u.emoji)

    return emoji_map


async def guild_roles(
    bot: DragonpawBot, guild: hikari.Guild
) -> Mapping[str, hikari.Role]:
    roles = await bot.rest.fetch_roles(guild=guild.id)
    return {r.name: r for r in roles}


async def guild_role_by_name(
    bot: DragonpawBot,
    guild: hikari.Guild | hikari.Snowflake,
    name: str,
) -> Optional[hikari.Role]:
    guild_id = guild.id if isinstance(guild, hikari.Guild) else guild
    roles = await bot.rest.fetch_roles(guild=guild_id)
    for r in roles:
        if r.name == name:
            return r
    return None


def member_has_role(member: hikari.Member, role_name: str) -> bool:
    """Check if a member has a role by name (via the guild's role cache)."""
    for role in member.get_roles():
        if role.name == role_name:
            return True
    return False


async def report_errors(
    bot: DragonpawBot,
    guild_id: hikari.Snowflake,
    error: str,
):
    """Dump all the config errors somewhere, where hopefully they get seen."""

    # BUG: This doesn't work on initial setup as there's no state file to read at first
    c = bot.state(guild_id)
    if not c:
        logger.error("Can't report errors on an unknown guild: %r", guild_id)
        logger.warning("Would have said: %s", error)
        return

    # Where to boss?
    if c.log_channel_id:
        to = c.log_channel_id
    elif c.role_channel_id:
        to = c.role_channel_id
    else:
        logger.error("G:%r No place to complain to: %s", c.name, error)
        return

    logger.error("G=%r %s", c.name, error)
    await bot.rest.create_message(
        channel=to,
        embed=hikari.Embed(
            color=SOLARIZED_RED,
            title="🤯 Oh Snap!",
            description=error,
        ),
    )
