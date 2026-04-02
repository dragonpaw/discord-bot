from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.validation import state as validation_state
from dragonpaw_bot.plugins.validation.models import ValidationStage

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)
loader = lightbulb.Loader()


@loader.task(lightbulb.crontrigger("15 * * * *"))  # every hour
async def validation_reminder_cron(
    bot: hikari.GatewayBot = lightbulb.di.INJECTED,
) -> None:
    """Ping unvalidated members in the lobby channel every 24h; kick after max_reminders."""
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

            for member in st.members:
                if member.stage != ValidationStage.AWAITING_RULES:
                    continue

                next_reminder = member.joined_at + timedelta(
                    hours=24 * (member.reminder_count + 1)
                )
                if now < next_reminder:
                    continue

                if member.reminder_count >= st.max_reminders:
                    await gc.kick_member(
                        member.user_id,
                        reason=f"Did not validate after {member.reminder_count} reminder(s)",
                    )
                    to_remove.append(member.user_id)
                else:
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
                            "Failed to send reminder",
                            user_id=member.user_id,
                            guild=guild.name,
                        )
                    else:
                        member.reminder_count += 1
                        logger.debug(
                            "Sent reminder",
                            user_id=member.user_id,
                            reminder_count=member.reminder_count,
                            guild=guild.name,
                        )

            if to_remove:
                st.members = [m for m in st.members if m.user_id not in to_remove]

            validation_state.save(st)
        except Exception:
            logger.exception("Error in reminder cron for guild", guild=guild.name)
