from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Mapping

import hikari
import lightbulb

from dragonpaw_bot import structs, utils
from dragonpaw_bot.colors import SOLARIZED_BLUE

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = logging.getLogger(__name__)
plugin = lightbulb.Plugin("Lobby")

RULES_AGREED_ID = "rules_agreed"


def load(bot: lightbulb.BotApp):
    bot.add_plugin(plugin)


def unload(bot: lightbulb.BotApp):
    bot.remove_plugin(plugin)


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
            row = plugin.bot.rest.build_message_action_row()
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


@plugin.listener(event=hikari.MemberCreateEvent, bind=True)
async def on_member_join(plugin: lightbulb.Plugin, event: hikari.MemberCreateEvent):
    """Handle a new member joining the server."""

    assert isinstance(plugin.bot, DragonpawBot)
    c = plugin.bot.state(event.guild_id)
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
            await utils.report_errors(
                bot=plugin.bot,
                guild_id=event.guild_id,
                error="Welcome message has an unknown substitution in it: {}".format(
                    str(e)
                ),
            )
            return

        await plugin.bot.rest.create_message(
            channel=c.lobby_channel_id,
            content=msg,
            user_mentions=True,
            role_mentions=True,
        )


@plugin.listener(event=hikari.InteractionCreateEvent, bind=True)
async def on_interaction(
    plugin: lightbulb.Plugin, event: hikari.InteractionCreateEvent
):
    if not isinstance(event.interaction, hikari.ComponentInteraction):
        return
    if not event.interaction.guild_id:
        return
    logger.debug("Interaction event: %r=%r", type(event), event)

    assert isinstance(plugin.bot, DragonpawBot)
    c = plugin.bot.state(event.interaction.guild_id)
    if not c:
        logger.error("Called on an unknown guild: %r", event.interaction.guild_id)
        return

    if event.interaction.custom_id == RULES_AGREED_ID and c.lobby_role_id:
        logger.info(
            "G:%s U:%s agreed to the rules, they are %s no more.",
            c.name,
            event.interaction.user.username,
            c.role_names[c.lobby_role_id],
        )
        await plugin.bot.rest.remove_role_from_member(
            guild=event.interaction.guild_id,
            user=event.interaction.user.id,
            role=c.lobby_role_id,
        )
        await event.interaction.create_initial_response(
            content="Thank you. Removing your {} role.".format(
                c.role_names[c.lobby_role_id]
            ),
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            flags=hikari.MessageFlag.EPHEMERAL,
        )
