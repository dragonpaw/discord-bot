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
    SUBDAY_SIGNUP_ID,
    TOTAL_WEEKS,
)

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
            logger.exception(
                "Error processing SubDay prompts for guild %r", guild.name
            )
