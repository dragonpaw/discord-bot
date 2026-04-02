"""Birthday plugin: member leave cleanup listener."""

from __future__ import annotations

from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.birthdays import state

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

loader = lightbulb.Loader()


@loader.listener(hikari.MemberDeleteEvent)
async def on_member_leave(event: hikari.MemberDeleteEvent) -> None:
    """Remove birthday entry when a member leaves the guild."""
    guild_id = int(event.guild_id)
    uid = int(event.user_id)

    try:
        guild_state = state.load(guild_id)
    except Exception:
        logger.exception(
            "Failed to load birthday state for member leave cleanup",
            guild_id=guild_id,
            user_id=uid,
        )
        return

    if uid not in guild_state.birthdays:
        return

    del guild_state.birthdays[uid]

    try:
        state.save(guild_state)
    except Exception:
        logger.exception(
            "Failed to save birthday state after member leave cleanup",
            guild_id=guild_id,
            user_id=uid,
        )
        return

    bot: DragonpawBot = event.app  # type: ignore[assignment]
    guild = event.get_guild()
    guild_name = guild.name if guild else str(event.guild_id)
    logger.warning(
        "Member left guild, removed birthday entry",
        guild=guild_name,
        user_id=uid,
    )

    gs = bot.state(event.guild_id)
    log_channel_id = gs.log_channel_id if gs else None
    gc = GuildContext(
        bot=bot,
        guild_id=event.guild_id,
        name=guild_name,
        log_channel_id=log_channel_id,
    )
    await gc.log(
        f"🎂 Removed birthday entry for <@{uid}> who left the server — I'll miss celebrating them! 🐾"
    )
