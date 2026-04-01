from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.validation import state as validation_state
from dragonpaw_bot.plugins.validation.commands import loader
from dragonpaw_bot.plugins.validation.models import ValidationStage

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)


@loader.task(lightbulb.crontrigger("0 * * * *"))  # every hour
async def validation_reminder_cron(bot: DragonpawBot = lightbulb.di.INJECTED) -> None:  # noqa: PLR0912
    """Ping unvalidated members in the lobby channel every 24h; kick after max_reminders."""
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
                    # Kick time
                    try:
                        await bot.rest.kick_user(
                            guild.id,
                            hikari.Snowflake(member.user_id),
                            reason="Did not validate in time",
                        )
                        await gc.log(
                            f"👢 *wry tail flick* Kicked <@{member.user_id}> after "
                            f"{member.reminder_count} reminder(s) with no response 🐉"
                        )
                        logger.info(
                            "Kicked unvalidated member",
                            user_id=member.user_id,
                            guild=guild.name,
                        )
                    except hikari.NotFoundError:
                        await gc.log(
                            f"👻 Tried to kick <@{member.user_id}> after "
                            f"{member.reminder_count} reminder(s), but they'd already left! 🐉"
                        )
                        logger.info(
                            "Unvalidated member already left",
                            user_id=member.user_id,
                            guild=guild.name,
                        )
                    except hikari.ForbiddenError:
                        await gc.log(
                            f"⚠️ Couldn't kick <@{member.user_id}> — please check my **Kick Members** permission! 🐉"
                        )
                        logger.warning(
                            "Cannot kick member — missing permission",
                            user_id=member.user_id,
                            guild=guild.name,
                        )
                    except hikari.HTTPError:
                        await gc.log(
                            f"⚠️ Something went wrong trying to kick <@{member.user_id}> — check the logs! 🐉"
                        )
                        logger.exception(
                            "Failed to kick unvalidated member",
                            user_id=member.user_id,
                            guild=guild.name,
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
