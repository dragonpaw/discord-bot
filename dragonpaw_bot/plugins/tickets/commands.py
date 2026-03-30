# dragonpaw_bot/plugins/tickets/commands.py
from __future__ import annotations

import re
from typing import TYPE_CHECKING

import hikari
import hikari.impl
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.tickets import state as tickets_state
from dragonpaw_bot.plugins.tickets.models import OpenTicket

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

TOPIC_MODAL_ID = "ticket_topic_modal"
TOPIC_INPUT_ID = "ticket_topic_input"


def _sanitize_channel_name(display_name: str) -> str:
    """Convert a display name to a valid Discord channel name: help-{name}."""
    name = display_name.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name)
    name = name.strip("-")
    return f"help-{name}"[:100]


class TicketCommand(
    lightbulb.SlashCommand,
    name="ticket",
    description="Open a private support ticket with the staff team.",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return

        bot: DragonpawBot = ctx.client.app  # type: ignore[assignment]
        st = tickets_state.load(int(ctx.guild_id))

        # Role gate
        if st.required_role_id:
            member = await bot.rest.fetch_member(ctx.guild_id, ctx.user)
            if hikari.Snowflake(st.required_role_id) not in member.role_ids:
                await ctx.respond(
                    "*snorts smoke* Hmm, I don't think you're allowed to open a ticket just yet! 🐉",
                    flags=hikari.MessageFlag.EPHEMERAL,
                )
                return

        # Duplicate ticket guard
        existing = next((t for t in st.open_tickets if t.user_id == ctx.user.id), None)
        if existing:
            await ctx.respond(
                f"*happy tail wag* You've already got a ticket open over in <#{existing.channel_id}>! Head over there~ 🐾",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        # Show modal
        topic_row = hikari.impl.ModalActionRowBuilder()
        topic_row.add_text_input(
            TOPIC_INPUT_ID,
            "What do you need help with today?",
            placeholder="Tell me what's going on and I'll chomp right on it~ 🐾",
            required=True,
            min_length=1,
            max_length=200,
        )
        await ctx.interaction.create_modal_response(
            title="*flaps wings* What's the snack? 🐉",
            custom_id=TOPIC_MODAL_ID,
            components=[topic_row],
        )


async def handle_topic_modal(interaction: hikari.ModalInteraction) -> None:
    """Handle ticket topic modal submission — create channel + ping staff."""
    if not interaction.guild_id or not interaction.member:
        return

    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    gc = GuildContext.from_interaction(interaction)  # type: ignore[arg-type]
    st = tickets_state.load(int(interaction.guild_id))

    # Extract topic from modal
    topic: str | None = None
    for row in interaction.components:
        for component in row.components:
            if component.custom_id == TOPIC_INPUT_ID:
                topic = component.value
    if not topic:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="*confused head tilt* I didn't catch a topic — please try `/ticket` again! 🐉",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    # Defer — channel creation takes a moment
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.DEFERRED_MESSAGE_CREATE,
        flags=hikari.MessageFlag.EPHEMERAL,
    )

    # Race guard: check again after defer
    existing = next(
        (t for t in st.open_tickets if t.user_id == interaction.user.id), None
    )
    if existing:
        await interaction.edit_initial_response(
            content=f"*happy tail wag* Looks like your ticket just got opened: <#{existing.channel_id}>! 🐾"
        )
        return

    # Build permission overwrites
    everyone_id = interaction.guild_id  # @everyone role ID == guild ID
    overwrites = [
        hikari.PermissionOverwrite(
            type=hikari.PermissionOverwriteType.ROLE,
            id=everyone_id,
            deny=hikari.Permissions.VIEW_CHANNEL,
            allow=hikari.Permissions.NONE,
        ),
        hikari.PermissionOverwrite(
            type=hikari.PermissionOverwriteType.MEMBER,
            id=interaction.user.id,
            allow=(
                hikari.Permissions.VIEW_CHANNEL
                | hikari.Permissions.SEND_MESSAGES
                | hikari.Permissions.READ_MESSAGE_HISTORY
            ),
            deny=hikari.Permissions.NONE,
        ),
    ]
    if bot.user_id:
        overwrites.append(
            hikari.PermissionOverwrite(
                type=hikari.PermissionOverwriteType.MEMBER,
                id=bot.user_id,
                allow=(
                    hikari.Permissions.VIEW_CHANNEL
                    | hikari.Permissions.SEND_MESSAGES
                    | hikari.Permissions.MANAGE_MESSAGES
                    | hikari.Permissions.MANAGE_CHANNELS
                ),
                deny=hikari.Permissions.NONE,
            )
        )
    if st.staff_role_id:
        overwrites.append(
            hikari.PermissionOverwrite(
                type=hikari.PermissionOverwriteType.ROLE,
                id=hikari.Snowflake(st.staff_role_id),
                allow=(
                    hikari.Permissions.VIEW_CHANNEL
                    | hikari.Permissions.SEND_MESSAGES
                    | hikari.Permissions.READ_MESSAGE_HISTORY
                ),
                deny=hikari.Permissions.NONE,
            )
        )

    channel_name = _sanitize_channel_name(interaction.member.display_name)

    try:
        channel = await bot.rest.create_guild_text_channel(
            guild=interaction.guild_id,
            name=channel_name,
            category=hikari.Snowflake(st.category_id)
            if st.category_id
            else hikari.UNDEFINED,
            permission_overwrites=overwrites,
        )
    except hikari.ForbiddenError:
        gc.logger.exception("Cannot create ticket channel — missing permissions")
        await gc.log(
            "🤯 *snorts smoke* I tried to open a ticket channel but I don't have permission to create channels! "
            "Please check that I have Manage Channels. 🐉"
        )
        await interaction.edit_initial_response(
            content="*sad smoke puff* I couldn't open a ticket channel — looks like I'm missing permissions. Please let staff know! 🐉"
        )
        return
    except hikari.HTTPError:
        gc.logger.exception("Failed to create ticket channel")
        await interaction.edit_initial_response(
            content="*sad smoke puff* Something went wrong opening your ticket. Please try again! 🐉"
        )
        return

    # Persist ticket — re-load state to capture any concurrent mutations since the initial load
    st = tickets_state.load(int(interaction.guild_id))
    st.open_tickets.append(
        OpenTicket(
            user_id=int(interaction.user.id),
            channel_id=int(channel.id),
            topic=topic,
        )
    )
    tickets_state.save(st)

    # Post welcome message in ticket channel
    staff_ping = f"<@&{st.staff_role_id}>" if st.staff_role_id else "Hey staff!"
    buttons_row = bot.rest.build_message_action_row()
    buttons_row.add_interactive_button(
        hikari.ButtonStyle.DANGER, "ticket_close", label="Close Ticket 🔒"
    )
    buttons_row.add_interactive_button(
        hikari.ButtonStyle.SECONDARY, "ticket_add_person", label="Add Person 👤"
    )

    await bot.rest.create_message(
        channel=channel.id,
        content=(
            f"*flaps wings excitedly* A new ticket has landed! 🎫\n\n"
            f"{staff_ping}\n\n"
            f"**{interaction.member.display_name}** needs help with: **{topic}**"
        ),
        components=[buttons_row],
    )

    await interaction.edit_initial_response(
        content=f"*happy tail wag* I've opened a cozy little ticket channel for you: <#{channel.id}> — hop on in! 🐉"
    )

    gc.logger.info("Opened ticket", channel=channel_name, topic=topic)
    await gc.log(
        f"🎫 *happy flap* I just opened a cozy little ticket for {interaction.user.mention}! "
        f'They need help with: "{topic}" 🐾'
    )


async def handle_ticket_close(interaction: hikari.ComponentInteraction) -> None:
    """Respond with a confirmation prompt."""
    channel_id = int(interaction.channel_id)
    row = interaction.app.rest.build_message_action_row()
    row.add_interactive_button(
        hikari.ButtonStyle.DANGER,
        f"ticket_close_confirm:{channel_id}",
        label="Yes, close it 🔒",
    )
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_CREATE,
        content=(
            "*peers at you with big dragon eyes* Are you sure you want to close this ticket? "
            "It'll be gone for good! 🐉"
        ),
        flags=hikari.MessageFlag.EPHEMERAL,
        component=row,
    )


async def handle_ticket_close_confirm(interaction: hikari.ComponentInteraction) -> None:
    """Delete the ticket channel and clean up state."""
    if not interaction.guild_id:
        return

    # ACK the button immediately — channel deletion takes a moment
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.DEFERRED_MESSAGE_UPDATE,
    )

    channel_id_str = interaction.custom_id.removeprefix("ticket_close_confirm:")
    try:
        channel_id = int(channel_id_str)
    except ValueError:
        return

    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    gc = GuildContext.from_interaction(interaction)
    st = tickets_state.load(int(interaction.guild_id))

    ticket = next((t for t in st.open_tickets if t.channel_id == channel_id), None)
    opener_mention = f"<@{ticket.user_id}>" if ticket else "someone"

    st.open_tickets = [t for t in st.open_tickets if t.channel_id != channel_id]
    tickets_state.save(st)

    try:
        await bot.rest.delete_channel(channel_id)
    except hikari.NotFoundError:
        pass  # Channel already gone — that's fine
    except hikari.ForbiddenError:
        gc.logger.exception("Cannot delete ticket channel", channel_id=channel_id)
        await gc.log(
            f"🤯 I tried to close a ticket channel (<#{channel_id}>) but couldn't delete it — "
            f"please check my Manage Channels permission! 🐉"
        )
        return

    gc.logger.info("Closed ticket", channel_id=channel_id)
    await gc.log(
        f"🔒 *nom nom* Ticket for {opener_mention} got all wrapped up and closed by "
        f"{interaction.user.mention} — all tidied away! 🐾"
    )


async def handle_ticket_add_person(interaction: hikari.ComponentInteraction) -> None:
    """Show a user select menu to add someone to the ticket."""
    select_row = interaction.app.rest.build_message_action_row()
    select_row.add_select_menu(
        hikari.ComponentType.USER_SELECT_MENU,
        "ticket_add_person_select",
    )
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_CREATE,
        content="*wiggles tail* Who should I let into this ticket? 👤",
        flags=hikari.MessageFlag.EPHEMERAL,
        component=select_row,
    )


async def handle_ticket_add_person_select(
    interaction: hikari.ComponentInteraction,
) -> None:
    """Grant channel access to the selected user."""
    if not interaction.guild_id or not interaction.values:
        return

    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    selected_user_id = hikari.Snowflake(interaction.values[0])
    channel_id = interaction.channel_id

    try:
        await bot.rest.edit_permission_overwrite(
            channel=channel_id,
            target=selected_user_id,
            target_type=hikari.PermissionOverwriteType.MEMBER,
            allow=(
                hikari.Permissions.VIEW_CHANNEL
                | hikari.Permissions.SEND_MESSAGES
                | hikari.Permissions.READ_MESSAGE_HISTORY
            ),
        )
    except hikari.ForbiddenError:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="*sad smoke puff* I couldn't add them — missing permissions! 🐉",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    await bot.rest.create_message(
        channel=channel_id,
        content=f"*nom* I've let <@{selected_user_id}> into the ticket! 🐾",
    )
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_CREATE,
        content=f"Done! <@{selected_user_id}> can now see this ticket 🐉",
        flags=hikari.MessageFlag.EPHEMERAL,
    )
