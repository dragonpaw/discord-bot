from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.validation import state as validation_state
from dragonpaw_bot.plugins.validation.commands import (
    MAX_VALIDATION_DAYS,
    RULES_AGREED_PREFIX,
    _close_validate_channel,
    _deadline_timestamp,
)
from dragonpaw_bot.plugins.validation.models import ValidationStage

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)
loader = lightbulb.Loader()

REMINDER_INTERVAL_HOURS = 16


def _build_rules_button_row(
    bot: hikari.GatewayBot, user_id: int
) -> hikari.api.MessageActionRowBuilder:
    """Build a fresh 'I've read the rules' button for the given member."""
    row = bot.rest.build_message_action_row()
    row.add_interactive_button(
        hikari.ButtonStyle.SUCCESS,
        f"{RULES_AGREED_PREFIX}{user_id}",
        label="I've read the rules! ✅",
    )
    return row


async def validation_reminder_cron(bot: hikari.GatewayBot) -> None:  # noqa: PLR0912
    """Ping unvalidated members every 16h; kick and close channel after 4 days."""
    bot = cast("DragonpawBot", bot)
    now = datetime.now(UTC)
    guilds = list(bot.cache.get_guilds_view().values())

    for guild in guilds:
        try:
            st = validation_state.load(int(guild.id))
            if not st.lobby_channel_id:
                continue

            gc = GuildContext.from_guild(bot, guild)
            deadline = timedelta(days=MAX_VALIDATION_DAYS)

            for member in st.members:
                if member.stage == ValidationStage.AWAITING_STAFF:
                    continue

                if now >= member.joined_at + deadline:
                    # Drop from state and persist *before* kicking. The kick fires a
                    # MemberDeleteEvent; without this, on_member_leave treats our own
                    # kick as a voluntary departure — a confusing "flew away" staff
                    # log and a redundant channel close. kick_member logs the kick.
                    st.members = [m for m in st.members if m.user_id != member.user_id]
                    validation_state.save(st)
                    await gc.kick_member(
                        member.user_id,
                        reason=f"Did not complete validation within {MAX_VALIDATION_DAYS} days",
                    )
                    if member.channel_id:
                        await _close_validate_channel(
                            gc,
                            member.channel_id,
                            f"*puffs a small smoke ring* ⏰ Hey <@{member.user_id}> — "
                            f"your {MAX_VALIDATION_DAYS}-day validation window has closed. "
                            f"This channel will disappear shortly. "
                            f"You're welcome to rejoin the server and try again! 🐉",
                        )
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
                                f"smack the button below when you're ready~ 🐾\n\n"
                                f"⏳ You've got until {_deadline_timestamp(member.joined_at)} before "
                                f"I have to boop you back out of the nest!"
                            ),
                            components=[_build_rules_button_row(bot, member.user_id)],
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
                                f"photos in here when you're ready~ 🐾\n\n"
                                f"⏳ You've got until {_deadline_timestamp(member.joined_at)} before "
                                f"I have to boop you back out of the nest!"
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

            validation_state.save(st)
        except Exception:
            logger.exception("Error in validation cron for guild", guild=guild.name)


@loader.task(lightbulb.crontrigger("15 * * * *"))  # every hour
async def _validation_reminder_cron_task(
    bot: hikari.GatewayBot = lightbulb.di.INJECTED,
) -> None:
    await validation_reminder_cron(bot)
