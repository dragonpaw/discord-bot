from __future__ import annotations

from typing import TYPE_CHECKING, List, Mapping

import hikari
import lightbulb
import structlog

from dragonpaw_bot import structs, utils
from dragonpaw_bot.colors import SOLARIZED_BLUE
from dragonpaw_bot.utils import ChannelContext, GuildContext, InteractionHandler

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

loader = lightbulb.Loader()

RULES_AGREED_ID = "rules_agreed"


async def configure_lobby(
    gc: GuildContext,
    config: structs.LobbyConfig,
    state: structs.GuildState,
    role_map: Mapping[str, hikari.Role],
) -> List[str]:
    log = gc.logger
    errors: List[str] = []

    # Where is the lobby
    channel = await utils.guild_channel_by_name(gc, config.channel)
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
        cc = ChannelContext.from_entry(
            gc,
            type(
                "_Entry",
                (),
                {"channel_id": int(channel.id), "channel_name": channel.name or ""},
            )(),
        )
        await cc.delete_my_messages()

        embed = hikari.Embed(
            title="Server Rules",
            description=config.rules,
            color=SOLARIZED_BLUE,
        )

        state.lobby_rules = config.rules
        state.lobby_click_for_rules = config.click_for_rules

        if config.click_for_rules and config.role:
            row = gc.bot.rest.build_message_action_row()
            row.add_interactive_button(
                hikari.ButtonStyle.SUCCESS,
                RULES_AGREED_ID,
                emoji="✅",
                label="I agree",
            )
            await channel.send(embed=embed, component=row)
        else:
            await channel.send(embed=embed)

    log.info("Configured lobby channel", channel=config.channel)
    return errors


@loader.listener(hikari.MemberCreateEvent)
async def on_member_join(event: hikari.MemberCreateEvent):
    """Handle a new member joining the server."""
    bot: DragonpawBot = event.app  # type: ignore[assignment]

    c = bot.state(event.guild_id)
    if not c:
        logger.error(
            "on_member_join called on an unknown guild", guild_id=int(event.guild_id)
        )
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
            guild = event.get_guild()
            guild_name = guild.name if guild else str(event.guild_id)
            gs = bot.state(event.guild_id)
            log_channel_id = gs.log_channel_id if gs else None
            gc = GuildContext(
                bot=bot,
                guild_id=event.guild_id,
                name=guild_name,
                log_channel_id=log_channel_id,
            )
            await gc.log(
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
        logger.error(
            "handle_rules_agreed called on an unknown guild",
            guild_id=int(interaction.guild_id),
        )
        return

    if not guild_state.lobby_role_id:
        return

    # Respond immediately per Discord 3-second timeout rule
    await interaction.create_initial_response(
        content="*happy tail wag* 🐉 Welcome in! Let me get that lobby role off you~",
        response_type=hikari.ResponseType.MESSAGE_CREATE,
        flags=hikari.MessageFlag.EPHEMERAL,
    )

    logger.info("User agreed to the rules")

    try:
        await bot.rest.remove_role_from_member(
            guild=interaction.guild_id,
            user=interaction.user.id,
            role=guild_state.lobby_role_id,
        )
    except hikari.ForbiddenError:
        logger.error(
            "Cannot remove lobby role — forbidden",
            role_id=guild_state.lobby_role_id,
        )
        gc = GuildContext.from_interaction(interaction)
        await gc.log(
            f"🤯 Unable to remove lobby role from **{interaction.user.mention}**. "
            "Check bot role hierarchy permissions.",
        )


INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    RULES_AGREED_ID: handle_rules_agreed,
}
