from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Mapping

import hikari
import lightbulb

from dragonpaw_bot import structs, utils
from dragonpaw_bot.colors import SOLARIZED_BLUE
from dragonpaw_bot.utils import InteractionHandler

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = logging.getLogger(__name__)

loader = lightbulb.Loader()

RULES_AGREED_ID = "rules_agreed"


async def configure_lobby(
    bot: DragonpawBot,
    guild: hikari.Guild,
    config: structs.LobbyConfig,
    state: structs.GuildState,
    role_map: Mapping[str, hikari.Role],
) -> List[str]:
    errors: List[str] = []

    # Where is the lobby
    channel = await utils.guild_channel_by_name(
        bot=bot, guild=guild, name=config.channel
    )
    if not channel:
        errors.append(f"Lobby channel {config.channel} doesn't seem to exist.")
        return errors

    state.lobby_channel_id = channel.id

    # Does it have an auto-join role?
    if config.role:
        if config.role in role_map:
            state.lobby_role_id = role_map[config.role].id
        else:
            errors.append(f"The lobby role {config.role} doesn't seem to exist.")

    if config.kick_after_days:
        state.lobby_kick_days = config.kick_after_days

    if config.welcome_message:
        state.lobby_welcome_message = config.welcome_message

    if config.click_for_rules and not config.role:
        errors.append(
            "The lobby has a click-through rules, but no role to "
            "remove when they click.."
        )

    if config.rules:
        await utils.delete_my_messages(
            bot=bot, guild_name=guild.name, channel_id=channel.id
        )

        embed = hikari.Embed(
            title="Server Rules",
            description=config.rules,
            color=SOLARIZED_BLUE,
        )

        state.lobby_rules = config.rules
        state.lobby_click_for_rules = config.click_for_rules

        if config.click_for_rules and config.role:
            row = bot.rest.build_message_action_row()
            row.add_interactive_button(
                hikari.ButtonStyle.SUCCESS,
                RULES_AGREED_ID,
                emoji="✅",
                label="I agree",
            )
            await channel.send(embed=embed, component=row)
        else:
            await channel.send(embed=embed)

    logger.info("G=%r Configured lobby channel %s", guild.name, config.channel)
    return errors


@loader.listener(hikari.MemberCreateEvent)
async def on_member_join(event: hikari.MemberCreateEvent):
    """Handle a new member joining the server."""
    bot: DragonpawBot = event.app  # type: ignore[assignment]

    c = bot.state(event.guild_id)
    if not c:
        logger.error("Called on an unknown guild: %r", event.guild_id)
        return

    # Is there a on-join role configured
    if c.lobby_role_id:
        await event.member.add_role(
            role=c.lobby_role_id,
            reason="New member role",
        )

    # Is there a welcome message?
    if c.lobby_welcome_message and c.lobby_channel_id:
        try:
            msg = c.lobby_welcome_message.format(
                name=event.user.mention,
                days=c.lobby_kick_days,
            )
        except KeyError as e:
            await utils.log_to_guild(
                bot,
                event.guild_id,
                f"🤯 **Lobby error:** Welcome message has an unknown substitution: {e}",
            )
            return

        await bot.rest.create_message(
            channel=c.lobby_channel_id,
            content=msg,
            user_mentions=True,
            role_mentions=True,
        )


async def handle_rules_agreed(interaction: hikari.ComponentInteraction) -> None:
    """Handle the 'I agree' button click from the lobby rules message."""
    if not interaction.guild_id:
        return

    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    guild_state = bot.state(interaction.guild_id)
    if not guild_state:
        logger.error("Called on an unknown guild: %r", interaction.guild_id)
        return

    if not guild_state.lobby_role_id:
        return

    logger.info(
        "G=%r U=%r: Agreed to the rules, they are %s no more.",
        guild_state.name,
        interaction.user.username,
        guild_state.role_names[guild_state.lobby_role_id],
    )
    await bot.rest.remove_role_from_member(
        guild=interaction.guild_id,
        user=interaction.user.id,
        role=guild_state.lobby_role_id,
    )
    await interaction.create_initial_response(
        content=f"Thank you. Removing your {guild_state.role_names[guild_state.lobby_role_id]} role.",
        response_type=hikari.ResponseType.MESSAGE_CREATE,
        flags=hikari.MessageFlag.EPHEMERAL,
    )


INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    RULES_AGREED_ID: handle_rules_agreed,
}
