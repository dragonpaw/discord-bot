"""Intros plugin: live removal of the missing-intro role when a member posts."""

from __future__ import annotations

from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.intros import state as intros_state

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot
    from dragonpaw_bot.plugins.intros.models import IntrosGuildState

logger = structlog.get_logger(__name__)
loader = lightbulb.Loader()


@loader.listener(hikari.GuildMessageCreateEvent)
async def on_intro_post(event: hikari.GuildMessageCreateEvent) -> None:
    """Remove the missing-intro role the moment a flagged member posts."""
    try:
        await _handle_intro_post(event)
    except Exception:
        logger.exception("Error handling intro post", guild_id=int(event.guild_id))


async def _handle_intro_post(event: hikari.GuildMessageCreateEvent) -> None:
    if event.message.author.is_bot:
        return

    bot: DragonpawBot = event.app  # type: ignore[assignment]
    st = intros_state.load(int(event.guild_id))
    if (
        st.channel_id is None
        or st.missing_role_id is None
        or int(event.channel_id) != st.channel_id
    ):
        return

    member = event.member or bot.cache.get_member(event.guild_id, event.author_id)
    if member is None or st.missing_role_id not in [int(r) for r in member.role_ids]:
        return

    if not await _remove_missing_role(bot, event.guild_id, member, st):
        return

    logger.info(
        "Removed missing-intro role after post",
        guild=st.guild_name,
        user=member.display_name,
        role=st.missing_role_name,
    )

    guild = bot.cache.get_guild(event.guild_id)
    if guild is not None:
        gc = GuildContext.from_guild(bot, guild)
        await gc.log(
            f"📝 *happy chirp* **{member.display_name}** just posted their intro — I plucked the "
            f"**{st.missing_role_name}** role right off them! Welcome to the hoard! 🐾"
        )


async def _remove_missing_role(
    bot: DragonpawBot,
    guild_id: hikari.Snowflakeish,
    member: hikari.Member,
    st: IntrosGuildState,
) -> bool:
    """Strip the missing-intro role. Returns True if removed."""
    assert st.missing_role_id is not None
    try:
        await bot.rest.remove_role_from_member(guild_id, member.id, st.missing_role_id)
    except hikari.NotFoundError:
        return False
    except hikari.ForbiddenError:
        logger.warning(
            "Cannot remove missing-intro role on post",
            guild=st.guild_name,
            user=member.display_name,
            role=st.missing_role_name,
        )
        return False
    except hikari.HTTPError:
        logger.warning(
            "HTTP error removing missing-intro role on post",
            guild=st.guild_name,
            user=member.display_name,
        )
        return False
    return True
