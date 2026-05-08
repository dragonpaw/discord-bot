from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.validation import state as validation_state
from dragonpaw_bot.plugins.validation.commands import _close_validate_channel
from dragonpaw_bot.plugins.validation.models import ValidationStage

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)
loader = lightbulb.Loader()

REMINDER_INTERVAL_HOURS = 18
MAX_VALIDATION_DAYS = 7


async def validation_reminder_cron(bot: hikari.GatewayBot) -> None:
    """Ping unvalidated members every 18h; kick and close channel after 7 days."""
    bot = cast("DragonpawBot", bot)
    now = datetime.now(UTC)
    guilds = list(bot.cache.get_guilds_view().values())

    for guild in guilds:
        try:
            st = validation_state.load(int(guild.id))
            if not st.lobby_channel_id:
                continue

            gc = GuildContext.from_guild(bot, guild)
            to_remove: list[int] = []
            deadline = timedelta(days=MAX_VALIDATION_DAYS)

            for member in st.members:
                if member.stage == ValidationStage.AWAITING_STAFF:
                    continue

                if now >= member.joined_at + deadline:
                    await gc.kick_member(
                        member.user_id,
                        reason=f"Did not complete validation within {MAX_VALIDATION_DAYS} days",
                    )
                    if member.channel_id:
                        asyncio.create_task(
                            _close_validate_channel(
                                gc,
                                member.channel_id,
                                f"*puffs a small smoke ring* ⏰ Hey <@{member.user_id}> — "
                                f"your {MAX_VALIDATION_DAYS}-day validation window has closed. "
                                f"This channel will disappear shortly. "
                                f"You're welcome to rejoin the server and try again! 🐉",
                            )
                        )
                    to_remove.append(member.user_id)
                    continue

                next_reminder = member.joined_at + timedelta(
                    hours=REMINDER_INTERVAL_HOURS * (member.reminder_count + 1)
                )
                if now < next_reminder:
                    continue

                if member.stage == ValidationStage.AWAITING_RULES:
                    try:
                        await bot.rest.create_message(
                            channel=st.lobby_channel_id,
                            content=(
                                f"*gentle nudge* Hey <@{member.user_id}>! 🐉 Just a little reminder — "
                                f"you haven't finished reading the rules yet! Give 'em a read and "
                                f"click the button in my earlier message when you're ready~ 🐾"
                            ),
                        )
                    except hikari.HTTPError:
                        logger.warning(
                            "Failed to send lobby reminder",
                            user_id=member.user_id,
                            guild=guild.name,
                        )
                    else:
                        member.reminder_count += 1
                        logger.debug(
                            "Sent lobby reminder",
                            user_id=member.user_id,
                            reminder_count=member.reminder_count,
                            guild=guild.name,
                        )
                elif (
                    member.stage == ValidationStage.AWAITING_PHOTOS
                    and member.channel_id
                ):
                    try:
                        await bot.rest.create_message(
                            channel=member.channel_id,
                            content=(
                                f"*peers in curiously* Hey <@{member.user_id}>! 🐉 Don't forget — "
                                f"I'm still waiting for your verification photos! Drop at least 2 "
                                f"photos in here when you're ready~ 🐾"
                            ),
                        )
                    except hikari.HTTPError:
                        logger.warning(
                            "Failed to send photo reminder",
                            user_id=member.user_id,
                            guild=guild.name,
                        )
                    else:
                        member.reminder_count += 1
                        logger.debug(
                            "Sent photo reminder",
                            user_id=member.user_id,
                            reminder_count=member.reminder_count,
                            guild=guild.name,
                        )

            if to_remove:
                st.members = [m for m in st.members if m.user_id not in to_remove]

            validation_state.save(st)
        except Exception:
            logger.exception("Error in validation cron for guild", guild=guild.name)


@loader.task(lightbulb.crontrigger("15 * * * *"))  # every hour
async def _validation_reminder_cron_task(
    bot: hikari.GatewayBot = lightbulb.di.INJECTED,
) -> None:
    await validation_reminder_cron(bot)
