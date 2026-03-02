from __future__ import annotations

import asyncio
import datetime
import logging
from typing import TYPE_CHECKING

import hikari

from dragonpaw_bot.plugins.subday import prompts, state
from dragonpaw_bot.plugins.subday.constants import TOTAL_WEEKS

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = logging.getLogger(__name__)
SUNDAY_SEND_HOUR = 14  # UTC
SUNDAY_WEEKDAY = 6  # Monday=0 ... Sunday=6

_scheduler_task: asyncio.Task[None] | None = None


def start(bot: DragonpawBot) -> None:
    global _scheduler_task  # noqa: PLW0603
    _scheduler_task = asyncio.create_task(_sunday_loop(bot))
    logger.info("SubDay Sunday scheduler started")


def stop() -> None:
    global _scheduler_task  # noqa: PLW0603
    if _scheduler_task:
        _scheduler_task.cancel()
        _scheduler_task = None
        logger.info("SubDay Sunday scheduler stopped")


async def _sunday_loop(bot: DragonpawBot) -> None:
    """Check hourly; on Sunday at SUNDAY_SEND_HOUR UTC, advance eligible participants."""
    while True:
        try:
            now = datetime.datetime.now(tz=datetime.UTC)
            logger.debug(
                "Sunday loop tick: weekday=%d hour=%d (target: weekday=%d hour=%d)",
                now.weekday(),
                now.hour,
                SUNDAY_WEEKDAY,
                SUNDAY_SEND_HOUR,
            )
            if now.weekday() == SUNDAY_WEEKDAY and now.hour == SUNDAY_SEND_HOUR:
                logger.info("Sunday prompt run triggered at %s", now.isoformat())
                await _send_weekly_prompts(bot)
            await asyncio.sleep(3600)  # check every hour
        except asyncio.CancelledError:
            logger.info("SubDay Sunday loop cancelled, shutting down")
            return
        except Exception:
            logger.exception("Error in SubDay Sunday loop")
            await asyncio.sleep(3600)


async def _send_weekly_prompts(bot: DragonpawBot) -> None:
    """Advance completed participants and DM their next prompt."""
    guilds = list(bot.cache.get_guilds_view().values())
    logger.info("Sunday prompt run: processing %d guild(s)", len(guilds))

    for guild in guilds:
        guild_id = int(guild.id)
        guild_state = state.load(guild_id)

        if not guild_state.participants:
            logger.debug("G=%r: No SubDay participants, skipping", guild.name)
            continue

        logger.info(
            "G=%r: Processing %d SubDay participant(s)",
            guild.name,
            len(guild_state.participants),
        )
        guild_state.guild_name = guild.name
        changed = False

        to_remove: list[int] = []
        for uid, participant in guild_state.participants.items():
            if not participant.week_completed:
                logger.debug(
                    "G=%r U=%d: Week %d not completed, skipping",
                    guild.name,
                    uid,
                    participant.current_week,
                )
                continue

            if participant.current_week >= TOTAL_WEEKS:
                logger.debug(
                    "G=%r U=%d: Already at week %d (graduated), skipping",
                    guild.name,
                    uid,
                    participant.current_week,
                )
                continue

            # Advance to next week
            participant.current_week += 1
            participant.week_completed = False
            participant.week_sent = False
            changed = True
            logger.info(
                "G=%r U=%d: Advanced to week %d",
                guild.name,
                uid,
                participant.current_week,
            )

            # Check if member is still in the guild
            try:
                member = await bot.rest.fetch_member(guild.id, hikari.Snowflake(uid))
            except hikari.NotFoundError:
                logger.info(
                    "G=%r U=%d: Left the server, removing from SubDay",
                    guild.name,
                    uid,
                )
                to_remove.append(uid)
                continue

            # DM the new prompt
            if participant.current_week <= TOTAL_WEEKS:
                try:
                    prompt = prompts.load_week(participant.current_week)
                    embed = prompts.build_prompt_embed(prompt)
                    dm = await member.user.fetch_dm_channel()
                    await dm.send(embed=embed)
                    participant.week_sent = True
                    logger.info(
                        "G=%r U=%r: Sent SubDay week %d prompt",
                        guild.name,
                        member.username,
                        participant.current_week,
                    )
                except hikari.ForbiddenError:
                    logger.warning(
                        "G=%r U=%r: Cannot DM user for SubDay prompt (DMs disabled)",
                        guild.name,
                        member.username,
                    )
                except hikari.HTTPError as exc:
                    logger.error(
                        "G=%r U=%r: Failed to DM SubDay prompt: %s",
                        guild.name,
                        member.username,
                        exc,
                    )

            await asyncio.sleep(1)  # rate limit courtesy

        for uid in to_remove:
            del guild_state.participants[uid]
            changed = True

        if changed:
            state.save(guild_state)
            logger.info("G=%r: Sunday run complete, state saved", guild.name)
        else:
            logger.debug("G=%r: No changes this Sunday run", guild.name)
