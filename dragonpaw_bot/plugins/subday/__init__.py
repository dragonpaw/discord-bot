# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import hikari
import lightbulb

from dragonpaw_bot.plugins.subday import commands, prompts, state
from dragonpaw_bot.plugins.subday.constants import (
    MILESTONE_WEEKS,
    SUBDAY_CFG_ROLE_PREFIX,
    SUBDAY_CONFIG_PREFIX,
    SUBDAY_OWNER_PREFIX,
    SUBDAY_SIGNUP_ID,
    TOTAL_WEEKS,
)
from dragonpaw_bot.plugins.subday.models import SubDayParticipant

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

__all__ = ["MILESTONE_WEEKS", "TOTAL_WEEKS"]

logger = logging.getLogger(__name__)

loader = lightbulb.Loader()

subday_group = lightbulb.Group("subday", "Where I am Led — 52-week guided journal")


@subday_group.register
class SubDayHelp(
    lightbulb.SlashCommand,
    name="help",
    description="Show available SubDay commands",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        await commands.help_handler(ctx)


commands.register(subday_group)
loader.command(subday_group)


async def _respond_error(
    interaction: hikari.ComponentInteraction, message: str
) -> None:
    """Try to send an ephemeral error response; ignore if the interaction expired."""
    try:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content=message,
            flags=hikari.MessageFlag.EPHEMERAL,
        )
    except hikari.NotFoundError:
        pass  # Interaction already expired or was already responded to


@loader.listener(hikari.InteractionCreateEvent)
async def on_interaction(event: hikari.InteractionCreateEvent) -> None:
    if not isinstance(event.interaction, hikari.ComponentInteraction):
        return
    interaction = event.interaction
    cid = interaction.custom_id
    logger.debug(
        "Component interaction: custom_id=%r user=%r guild=%r",
        cid,
        interaction.user.username,
        interaction.guild_id,
    )
    if cid.startswith(SUBDAY_OWNER_PREFIX):
        try:
            await commands.handle_owner_interaction(interaction)
        except Exception:
            logger.exception(
                "Error handling owner interaction: user=%r custom_id=%r",
                interaction.user.username,
                cid,
            )
            await _respond_error(
                interaction, "An error occurred processing the owner request."
            )
        return
    if cid == SUBDAY_SIGNUP_ID:
        try:
            await commands.handle_signup_interaction(interaction)
        except Exception:
            logger.exception(
                "Error handling signup interaction: user=%r guild=%r",
                interaction.user.username,
                interaction.guild_id,
            )
            await _respond_error(interaction, "An error occurred during signup.")
        return
    if not (
        cid.startswith(SUBDAY_CONFIG_PREFIX) or cid.startswith(SUBDAY_CFG_ROLE_PREFIX)
    ):
        return
    try:
        await commands.handle_config_interaction(interaction)
    except Exception:
        logger.exception(
            "Error handling config interaction: custom_id=%r user=%r",
            cid,
            interaction.user.username,
        )
        await _respond_error(interaction, "An error occurred updating settings.")


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
    owner_changed = False
    for owner_id, sub_prompt_list in owner_prompts.items():
        # Verify owner is still in the guild
        try:
            await bot.rest.fetch_member(guild.id, hikari.Snowflake(owner_id))
        except hikari.NotFoundError:
            logger.info(
                "G=%r: Owner %d left the server, clearing owner references",
                guild.name,
                owner_id,
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
                    logger.info(
                        "G=%r: Sent owner %d prompt copy for sub %d (week %d)",
                        guild.name,
                        owner_id,
                        sub_uid,
                        prompt.week,
                    )
                except (hikari.ForbiddenError, hikari.HTTPError) as exc:
                    logger.warning(
                        "G=%r: Failed to DM owner %d prompt for sub %d: %s",
                        guild.name,
                        owner_id,
                        sub_uid,
                        exc,
                    )
                await asyncio.sleep(1)
        except (hikari.ForbiddenError, hikari.HTTPError) as exc:
            logger.warning(
                "G=%r: Cannot DM owner %d: %s",
                guild.name,
                owner_id,
                exc,
            )

    if owner_changed:
        state.save(guild_state)
        logger.info("G=%r: Saved state after clearing departed owners", guild.name)


async def _advance_participant(  # noqa: PLR0911
    bot: DragonpawBot,
    guild: hikari.Guild,
    uid: int,
    participant: SubDayParticipant,
    owner_prompts: dict[int, list[tuple[int, prompts.WeekPrompt]]],
) -> bool | None:
    """Advance one participant. Returns True if changed, None if should be removed."""
    if not participant.week_completed:
        logger.debug(
            "G=%r U=%d: Week %d not completed, skipping",
            guild.name,
            uid,
            participant.current_week,
        )
        return False

    if participant.current_week >= TOTAL_WEEKS:
        logger.debug(
            "G=%r U=%d: Already at week %d (graduated), skipping",
            guild.name,
            uid,
            participant.current_week,
        )
        return False

    # Check guild membership before mutating state
    try:
        member = await bot.rest.fetch_member(guild.id, hikari.Snowflake(uid))
    except hikari.NotFoundError:
        logger.info("G=%r U=%d: Left the server, removing from SubDay", guild.name, uid)
        return None  # sentinel: remove

    participant.current_week += 1
    participant.week_completed = False
    participant.week_sent = False
    logger.info(
        "G=%r U=%d: Advanced to week %d", guild.name, uid, participant.current_week
    )

    if participant.current_week <= TOTAL_WEEKS:
        try:
            prompt = prompts.load_week(participant.current_week)
            dm_embeds = prompts.build_weekly_dm_embeds(prompt)
            dm = await member.user.fetch_dm_channel()
            await dm.send(embeds=dm_embeds)
            participant.week_sent = True
            logger.info(
                "G=%r U=%r: Sent SubDay week %d prompt",
                guild.name,
                member.username,
                participant.current_week,
            )
            if participant.owner_id:
                owner_prompts.setdefault(participant.owner_id, []).append((uid, prompt))
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
    guild_id = int(guild.id)
    guild_state = state.load(guild_id)

    if not guild_state.participants:
        logger.debug("G=%r: No SubDay participants, skipping", guild.name)
        return

    logger.info(
        "G=%r: Processing %d SubDay participant(s)",
        guild.name,
        len(guild_state.participants),
    )
    guild_state.guild_name = guild.name
    changed = False
    to_remove: list[int] = []
    owner_prompts: dict[int, list[tuple[int, prompts.WeekPrompt]]] = {}

    for uid, participant in guild_state.participants.items():
        result = await _advance_participant(bot, guild, uid, participant, owner_prompts)
        if result is None:
            to_remove.append(uid)
            changed = True
        elif result:
            changed = True

    if to_remove:
        _cleanup_removed_participants(guild_state, to_remove)

    if changed:
        state.save(guild_state)
        logger.info("G=%r: Sunday run complete, state saved", guild.name)
    else:
        logger.debug("G=%r: No changes this Sunday run", guild.name)

    if owner_prompts:
        await _forward_owner_prompts(bot, guild, guild_state, owner_prompts)


@loader.task(lightbulb.crontrigger("0 14 * * 0"))
async def sunday_prompts(bot: hikari.GatewayBot) -> None:
    """Advance completed participants and DM their next prompt."""
    assert isinstance(bot, DragonpawBot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.info("Sunday prompt run: processing %d guild(s)", len(guilds))
    for guild in guilds:
        try:
            await _process_guild_prompts(bot, guild)
        except Exception:
            logger.exception("Error processing SubDay prompts for guild %r", guild.name)
