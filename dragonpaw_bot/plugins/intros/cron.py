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
async def intros_daily_cleanup(bot: hikari.GatewayBot) -> None:
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
            f"⚠️ *sniffs around the intros channel* I'm missing **{', '.join(missing)}** "
            f"in <#{st.channel_id}> and can't tidy up old posts from members who've left. "
            f"Could someone fix my permissions? 🐉"
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
            f"🧹 *tidies the den* I nom'd {len(removed)} intro post(s) from members who've left: {names}."
        )


@loader.task(lightbulb.crontrigger("0 20 * * 6"))
async def intros_weekly_naughty_list(bot: hikari.GatewayBot) -> None:
    """Weekly task: post naughty list of members who haven't introduced themselves."""
    bot = cast("DragonpawBot", bot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.debug("Intros weekly naughty list run", guild_count=len(guilds))

    for guild in guilds:
        try:
            await _naughty_list_guild(bot, guild)
        except Exception:
            logger.exception("Error during intros naughty list", guild=guild.name)


async def _naughty_list_guild(bot: DragonpawBot, guild: hikari.Guild) -> None:
    gc = GuildContext.from_guild(bot, guild)
    st = intros_state.load(int(guild.id))

    if st.channel_id is None:
        return

    bot_st = bot.state(guild.id)
    if not bot_st or not bot_st.general_channel_id:
        return

    # Collect user IDs who have posted in the intros channel
    posted_ids: set[int] = set()
    async for message in bot.rest.fetch_messages(st.channel_id):
        if not message.author.is_bot:
            posted_ids.add(int(message.author.id))

    # Collect eligible members (non-bot, with required role if configured)
    missing_members: list[hikari.Member] = []
    async for member in bot.rest.fetch_members(guild.id):
        if member.is_bot:
            continue
        if st.required_role_id is not None and st.required_role_id not in [
            int(r) for r in member.role_ids
        ]:
            continue
        if int(member.id) not in posted_ids:
            missing_members.append(member)

    logger.info(
        "Intros naughty list",
        guild=guild.name,
        missing_count=len(missing_members),
    )

    if not missing_members:
        try:
            await bot.rest.create_message(
                channel=bot_st.general_channel_id,
                content=(
                    "*does a happy wiggle* 🐉 Everyone in the hoard has posted an introduction — "
                    "I'm so proud of you all! Such good mammals! 🐾"
                ),
            )
        except hikari.HTTPError:
            logger.warning(
                "Failed to post naughty list all-clear",
                guild=guild.name,
                channel_id=int(bot_st.general_channel_id),
            )
        await gc.log("📋 *happy tail wag* Weekly intros check — everyone's posted! 🐉")
        return

    role_note = f" with role **{st.required_role_name}**" if st.required_role_id else ""
    mentions = " ".join(m.mention for m in missing_members)
    try:
        await bot.rest.create_message(
            channel=bot_st.general_channel_id,
            content=(
                f"*squints with clipboard* 🐉 Psst! These lovely folks{role_note} haven't introduced "
                f"themselves in <#{st.channel_id}> yet — give 'em a little nudge! 🐾\n{mentions}"
            ),
        )
    except hikari.HTTPError:
        logger.warning(
            "Failed to post naughty list",
            guild=guild.name,
            channel_id=int(bot_st.general_channel_id),
        )
        await gc.log(
            f"⚠️ Couldn't post the weekly intro naughty list in <#{bot_st.general_channel_id}>! 🐉"
        )
        return

    await gc.log(
        f"📋 Weekly intros check — **{len(missing_members)}** member(s){role_note} "
        f"still haven't posted in <#{st.channel_id}> 🐉"
    )
