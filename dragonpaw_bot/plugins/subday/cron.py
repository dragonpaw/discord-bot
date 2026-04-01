from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.subday import loader, prompts, state
from dragonpaw_bot.plugins.subday.constants import (
    TOTAL_WEEKS,
)

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot
    from dragonpaw_bot.plugins.subday.models import SubDayParticipant

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------- #
#                              Sunday cron task                                #
# ---------------------------------------------------------------------------- #


async def _forward_owner_prompts(
    bot: DragonpawBot,
    guild: hikari.Guild,
    guild_state: state.SubDayGuildState,
    owner_prompts: dict[int, list[tuple[int, prompts.WeekPrompt]]],
) -> None:
    """Forward prompt copies to owners after the main Sunday loop."""
    log = logger.bind(guild=guild.name)
    owner_changed = False
    for owner_id, sub_prompt_list in owner_prompts.items():
        # Verify owner is still in the guild
        try:
            await bot.rest.fetch_member(guild.id, hikari.Snowflake(owner_id))
        except hikari.NotFoundError:
            log.info(
                "Owner left the server, clearing owner references",
                owner_id=owner_id,
            )
            for p in guild_state.participants.values():
                if p.owner_id == owner_id:
                    p.owner_id = None
            owner_changed = True
            continue

        # Send one DM per sub
        try:
            owner_user = await bot.rest.fetch_user(hikari.Snowflake(owner_id))
            dm = await owner_user.fetch_dm_channel()
            for sub_uid, prompt in sub_prompt_list:
                try:
                    owner_embeds = prompts.build_owner_dm_embeds(prompt, sub_uid)
                    await dm.send(embeds=owner_embeds)
                    log.info(
                        "Sent owner prompt copy for sub",
                        owner_id=owner_id,
                        sub_uid=sub_uid,
                        week=prompt.week,
                    )
                except (hikari.ForbiddenError, hikari.HTTPError) as exc:
                    log.warning(
                        "Failed to DM owner prompt for sub",
                        owner_id=owner_id,
                        sub_uid=sub_uid,
                        error=str(exc),
                    )
                await asyncio.sleep(1)
        except (hikari.ForbiddenError, hikari.HTTPError) as exc:
            log.warning(
                "Cannot DM owner",
                owner_id=owner_id,
                error=str(exc),
            )

    if owner_changed:
        state.save(guild_state)
        log.info("Saved state after clearing departed owners")


async def _advance_participant(
    bot: DragonpawBot,
    guild: hikari.Guild,
    uid: int,
    participant: SubDayParticipant,
    owner_prompts: dict[int, list[tuple[int, prompts.WeekPrompt]]],
) -> bool | None:
    """Advance one participant. Returns True if changed, None if should be removed."""
    log = logger.bind(guild=guild.name, user_id=uid)
    if not participant.week_completed:
        log.debug(
            "Week not completed, skipping",
            week=participant.current_week,
        )
        return False

    if participant.current_week >= TOTAL_WEEKS:
        log.debug(
            "Already graduated, skipping",
            week=participant.current_week,
        )
        return False

    # Check guild membership before mutating state
    try:
        member = await bot.rest.fetch_member(guild.id, hikari.Snowflake(uid))
    except hikari.NotFoundError:
        log.info("Left the server, removing from SubDay")
        return None  # sentinel: remove

    participant.current_week += 1
    participant.week_completed = False
    participant.week_sent = False
    participant.reminder_sent = False
    log.info("Advanced to week", week=participant.current_week)

    if participant.current_week <= TOTAL_WEEKS:
        try:
            prompt = prompts.load_week(participant.current_week)
            dm_embeds = prompts.build_weekly_dm_embeds(prompt)
            dm = await member.user.fetch_dm_channel()
            await dm.send(embeds=dm_embeds)
            participant.week_sent = True
            log.info(
                "Sent SubDay week prompt",
                week=participant.current_week,
            )
            if participant.owner_id:
                owner_prompts.setdefault(participant.owner_id, []).append((uid, prompt))
        except hikari.ForbiddenError:
            log.warning("Cannot DM user for SubDay prompt (DMs disabled)")
        except hikari.HTTPError as exc:
            log.warning("Failed to DM SubDay prompt", error=str(exc))

    await asyncio.sleep(1)
    return True


def _cleanup_removed_participants(
    guild_state: state.SubDayGuildState, to_remove: list[int]
) -> None:
    """Remove participants and clean up owner references pointing to them."""
    for uid in to_remove:
        del guild_state.participants[uid]
    removed_set = set(to_remove)
    for p in guild_state.participants.values():
        if p.owner_id in removed_set:
            p.owner_id = None
        if p.pending_owner_id in removed_set:
            p.pending_owner_id = None


async def _process_guild_prompts(bot: DragonpawBot, guild: hikari.Guild) -> None:
    """Process weekly prompts for a single guild."""
    log = logger.bind(guild=guild.name)
    guild_id = int(guild.id)
    guild_state = state.load(guild_id)

    if not guild_state.participants:
        log.debug("No SubDay participants, skipping")
        return

    log.info(
        "Processing SubDay participants",
        count=len(guild_state.participants),
    )
    guild_state.guild_name = guild.name
    changed = False
    to_remove: list[int] = []
    owner_prompts: dict[int, list[tuple[int, prompts.WeekPrompt]]] = {}
    advanced: list[tuple[int, int]] = []

    for uid, participant in guild_state.participants.items():
        old_week = participant.current_week
        result = await _advance_participant(bot, guild, uid, participant, owner_prompts)
        if result is None:
            to_remove.append(uid)
            changed = True
        elif result:
            changed = True
            if participant.current_week > old_week:
                advanced.append((uid, participant.current_week))

    if to_remove:
        _cleanup_removed_participants(guild_state, to_remove)

    if changed:
        state.save(guild_state)
        log.info("Sunday run complete, state saved")
    else:
        log.debug("No changes this Sunday run")

    if advanced:
        gc = GuildContext.from_guild(bot, guild)
        lines = [f"- <@{uid}> → Week {week}" for uid, week in advanced]
        await gc.log(
            f"📬 Sunday SubDay run complete! Sent next week's prompt to "
            f"{len(advanced)} participant(s):\n" + "\n".join(lines)
        )

    if owner_prompts:
        await _forward_owner_prompts(bot, guild, guild_state, owner_prompts)


@loader.task(lightbulb.crontrigger("0 14 * * 0"))
async def subday_sunday_prompts(bot: hikari.GatewayBot) -> None:
    """Advance completed participants and DM their next prompt."""
    bot = cast("DragonpawBot", bot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.info("Sunday prompt run", guild_count=len(guilds))
    for guild in guilds:
        try:
            await _process_guild_prompts(bot, guild)
        except Exception:
            logger.exception("Error processing SubDay prompts", guild=guild.name)


# ---------------------------------------------------------------------------- #
#                             Friday reminder cron                             #
# ---------------------------------------------------------------------------- #


async def _process_guild_friday_reminders(
    bot: DragonpawBot, guild: hikari.Guild
) -> None:
    """Send Friday reminders for participants who haven't completed their current week."""
    log = logger.bind(guild=guild.name)
    guild_id = int(guild.id)
    guild_state = state.load(guild_id)

    if not guild_state.participants:
        log.debug("No SubDay participants, skipping Friday reminders")
        return

    gc = GuildContext.from_guild(bot, guild)
    any_sent = False

    for uid, participant in guild_state.participants.items():
        if participant.week_completed or participant.reminder_sent:
            continue

        # Fetch member, skip if they left
        try:
            member = await bot.rest.fetch_member(guild.id, hikari.Snowflake(uid))
        except hikari.NotFoundError:
            log.info("Participant left server, skipping reminder", user_id=uid)
            continue

        # DM the sub
        try:
            dm = await member.user.fetch_dm_channel()
            await dm.send(
                f"*nuzzles gently* 🐉 Hey hey! Just a little Friday reminder — "
                f"you still have your **Week {participant.current_week}** journal to finish! "
                f"You've got this~ 💪🐾"
            )
            log.info(
                "Sent Friday reminder to sub",
                user_id=uid,
                week=participant.current_week,
            )
        except hikari.ForbiddenError:
            log.warning("Cannot DM sub for Friday reminder (DMs disabled)", user_id=uid)
        except hikari.HTTPError as exc:
            log.warning("Failed to DM sub Friday reminder", user_id=uid, error=str(exc))

        await asyncio.sleep(1)

        # DM the owner if set
        if participant.owner_id:
            try:
                owner_user = await bot.rest.fetch_user(
                    hikari.Snowflake(participant.owner_id)
                )
                owner_dm = await owner_user.fetch_dm_channel()
                await owner_dm.send(
                    f"*tugs on sleeve* 🐉 Psst! <@{uid}> hasn't finished their "
                    f"**Week {participant.current_week}** journal yet. "
                    f"Maybe give them a little nudge? 💜"
                )
                log.info(
                    "Sent Friday owner reminder",
                    owner_id=participant.owner_id,
                    sub_id=uid,
                    week=participant.current_week,
                )
            except hikari.ForbiddenError:
                log.warning(
                    "Cannot DM owner for Friday reminder (DMs disabled)",
                    owner_id=participant.owner_id,
                )
            except hikari.HTTPError as exc:
                log.warning(
                    "Failed to DM owner Friday reminder",
                    owner_id=participant.owner_id,
                    error=str(exc),
                )
            await asyncio.sleep(1)

        participant.reminder_sent = True
        any_sent = True
        await gc.log(
            f"🔔 Sent Friday reminder to {member.mention} for Week {participant.current_week}"
        )

    if any_sent:
        state.save(guild_state)
        log.info("Friday reminders complete, state saved")
    else:
        log.debug("No Friday reminders needed")


@loader.task(lightbulb.crontrigger("0 20 * * 5"))
async def subday_friday_reminders(bot: hikari.GatewayBot) -> None:
    """Friday noon PST (20:00 UTC): remind incomplete participants."""
    bot = cast("DragonpawBot", bot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.info("Friday reminder run", guild_count=len(guilds))
    for guild in guilds:
        try:
            await _process_guild_friday_reminders(bot, guild)
        except Exception:
            logger.exception("Error processing Friday reminders", guild=guild.name)
