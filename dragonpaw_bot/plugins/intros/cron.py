from __future__ import annotations

from typing import TYPE_CHECKING, cast

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import (
    CHANNEL_CLEANUP_PERMS,
    GuildContext,
    check_channel_perms,
)
from dragonpaw_bot.plugins.intros import loader
from dragonpaw_bot.plugins.intros import state as intros_state

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)


@loader.task(lightbulb.crontrigger("0 9 * * *"))
async def daily_intros_cleanup(bot: hikari.GatewayBot) -> None:
    """Daily task: delete intro posts from members who have left the guild."""
    bot = cast("DragonpawBot", bot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.debug("Intros daily cleanup run", guild_count=len(guilds))

    for guild in guilds:
        try:
            await _cleanup_guild(bot, guild)
        except Exception:
            logger.exception("Error during intros cleanup", guild=guild.name)


async def _cleanup_guild(bot: DragonpawBot, guild: hikari.Guild) -> None:
    gc = GuildContext.from_guild(bot, guild)
    st = intros_state.load(int(guild.id))

    if st.channel_id is None:
        return

    logger.debug("Checking intros channel", guild=guild.name, channel=st.channel_name)

    missing = await check_channel_perms(
        bot, guild.id, hikari.Snowflake(st.channel_id), CHANNEL_CLEANUP_PERMS
    )
    if missing:
        logger.warning(
            "Missing permissions in intros channel",
            guild=guild.name,
            channel=st.channel_name,
            missing=missing,
        )
        await gc.log(
            f"⚠️ I'm missing **{', '.join(missing)}** in <#{st.channel_id}> "
            f"and can't clean up departed members' intro posts. "
            f"Please fix my channel permissions."
        )
        return

    # Build set of current member IDs
    member_ids: set[int] = set()
    async for member in bot.rest.fetch_members(guild.id):
        member_ids.add(int(member.id))

    # Delete intro posts from members no longer in the guild
    removed: list[str] = []
    async for message in bot.rest.fetch_messages(st.channel_id):
        if int(message.author.id) not in member_ids:
            author_name = message.author.username
            try:
                await message.delete()
                removed.append(author_name)
                logger.info(
                    "Removed departed member's intro",
                    guild=guild.name,
                    user=author_name,
                )
            except hikari.ForbiddenError:
                logger.warning(
                    "Cannot delete intro message — missing permissions",
                    guild=guild.name,
                    user=author_name,
                )
            except hikari.NotFoundError:
                pass  # Already deleted

    if removed:
        names = ", ".join(f"**{n}**" for n in removed)
        await gc.log(
            f"🧹 Removed {len(removed)} intro post(s) from departed members: {names}."
        )
