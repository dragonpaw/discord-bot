# dragonpaw_bot/plugins/tickets/commands.py
from __future__ import annotations

import re
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot import journal
from dragonpaw_bot.context import (
    PRIVATE_CHANNEL_USER_PERMS,
    GuildContext,
    check_channel_perms,
    check_guild_perms,
)
from dragonpaw_bot.plugins.tickets import state as tickets_state
from dragonpaw_bot.plugins.tickets.models import OpenTicket, TicketGuildState

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

loader = lightbulb.Loader()

TOPIC_MODAL_ID = "ticket_topic_modal"
TOPIC_INPUT_ID = "ticket_topic_input"
TOPIC_MODAL_TITLE = "*flaps wings* What's the snack? 🐉"
TICKET_OPEN_ID = "ticket_open"
TICKET_FOR_SELECT_ID = "ticket_for_select"

TICKET_CREATE_PERMS: dict[hikari.Permissions, str] = {
    hikari.Permissions.MANAGE_CHANNELS: "Manage Channels",
}


def _find_open_ticket(
    st: TicketGuildState, user_id: hikari.Snowflakeish
) -> OpenTicket | None:
    """The user's open ticket in this guild, if any."""
    return next((t for t in st.open_tickets if t.user_id == user_id), None)


async def _check_create_perms(
    bot: DragonpawBot,
    guild_id: hikari.Snowflake,
    category_id: int | None,
) -> tuple[list[str], str]:
    """Return (missing_perm_names, scope_description) for ticket channel creation."""
    if category_id:
        missing = await check_channel_perms(
            bot, guild_id, hikari.Snowflake(category_id), TICKET_CREATE_PERMS
        )
        return missing, f"the ticket category (<#{category_id}>)"
    missing = await check_guild_perms(bot, guild_id, TICKET_CREATE_PERMS)
    return missing, "the server"


def _sanitize_channel_name(display_name: str) -> str:
    """Convert a display name to a valid Discord channel name: help-{name}."""
    name = re.sub(r"[\[({][^\[({]*[\])}].*$", "", display_name).strip()
    name = name.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name)
    name = name.strip("-")
    return f"help-{name}"[:100]


def _is_staff(st: TicketGuildState, member: hikari.Member) -> bool:
    """Holds the staff role, or owns the guild. Cache-only, so safe before a modal."""
    guild = member.get_guild()
    if guild is not None and guild.owner_id == member.id:
        return True
    return (
        st.staff_role_id is not None
        and hikari.Snowflake(st.staff_role_id) in member.role_ids
    )


def _ticket_block_reason(
    st: TicketGuildState, member: hikari.Member, user_id: hikari.Snowflake
) -> str | None:
    """Why this member can't open a ticket right now, phrased for them. None means go ahead.

    Reads only the cached member payload — no REST. Both callers may show a
    modal as their initial response, and a modal can't follow a deferred
    response, so they are bound by Discord's 3-second deadline.
    """
    if (
        st.required_role_id
        and hikari.Snowflake(st.required_role_id) not in member.role_ids
    ):
        return "*snorts smoke* Hmm, I don't think you're allowed to open a ticket just yet! 🐉"

    existing = _find_open_ticket(st, user_id)
    if existing:
        return (
            f"*happy tail wag* You've already got a ticket open over in "
            f"<#{existing.channel_id}>! Head over there~ 🐾"
        )

    return None


def _topic_modal_rows(
    *, on_behalf: bool = False
) -> list[hikari.impl.ModalActionRowBuilder]:
    if on_behalf:
        label = "What do they need help with?"
        placeholder = "They'll see this in the ticket, so be kind~ 🐾"
    else:
        label = "What do you need help with today?"
        placeholder = "Tell me what's going on and I'll chomp right on it~ 🐾"
    topic_row = hikari.impl.ModalActionRowBuilder()
    topic_row.add_text_input(
        TOPIC_INPUT_ID,
        label,
        placeholder=placeholder,
        required=True,
        min_length=1,
        max_length=200,
    )
    return [topic_row]


@loader.command
class AdultierAdultCommand(
    lightbulb.SlashCommand,
    name="adultier-adult",
    description="Open a private support ticket with the staff team.",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id or not ctx.member:
            return

        st = tickets_state.load(int(ctx.guild_id))

        # This command is in _AUTO_DEFER_EXCLUSIONS (see bot.py) so it can show
        # a modal as its initial response.
        reason = _ticket_block_reason(st, ctx.member, ctx.user.id)
        if reason:
            await ctx.respond(reason, flags=hikari.MessageFlag.EPHEMERAL)
            return

        await ctx.respond_with_modal(
            title=TOPIC_MODAL_TITLE,
            custom_id=TOPIC_MODAL_ID,
            components=_topic_modal_rows(),
        )


async def handle_ticket_open_button(interaction: hikari.ComponentInteraction) -> None:
    """Open the ticket topic modal from the button channel card."""
    if not interaction.guild_id or not interaction.member:
        return

    st = tickets_state.load(int(interaction.guild_id))

    if _is_staff(st, interaction.member):
        select_row = interaction.app.rest.build_message_action_row()
        select_row.add_select_menu(
            hikari.ComponentType.USER_SELECT_MENU,
            TICKET_FOR_SELECT_ID,
            placeholder="Pick a member (or yourself)",
        )
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="*perks up ears* Who should I dig this den out for? 🐉",
            flags=hikari.MessageFlag.EPHEMERAL,
            component=select_row,
        )
        return

    reason = _ticket_block_reason(st, interaction.member, interaction.user.id)
    if reason:
        logger.info("Ticket open button denied")
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content=reason,
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    await interaction.create_modal_response(
        title=TOPIC_MODAL_TITLE,
        custom_id=TOPIC_MODAL_ID,
        components=_topic_modal_rows(),
    )


async def handle_ticket_for_select(interaction: hikari.ComponentInteraction) -> None:
    """Staff picked who the ticket is for — show the topic modal for them.

    The required-role gate is skipped on purpose: staff opening a ticket for a
    member who hasn't got that role yet is a main reason this path exists.
    """
    if not interaction.guild_id or not interaction.member or not interaction.values:
        return

    st = tickets_state.load(int(interaction.guild_id))
    if not _is_staff(st, interaction.member):
        logger.warning("Ticket member picker used by non-staff")
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="*snorts smoke* Only the staff team can open tickets for other folks! 🐉",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    target_id = hikari.Snowflake(interaction.values[0])

    existing = _find_open_ticket(st, target_id)
    if existing:
        logger.info("Ticket for member denied — already has one")
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content=(
                f"*happy tail wag* <@{target_id}> already has a ticket open over in "
                f"<#{existing.channel_id}>! 🐾"
            ),
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    await interaction.create_modal_response(
        title=TOPIC_MODAL_TITLE,
        custom_id=f"{TOPIC_MODAL_ID}:{target_id}",
        components=_topic_modal_rows(on_behalf=target_id != interaction.user.id),
    )


async def _ticket_owner(
    bot: DragonpawBot, st: TicketGuildState, interaction: hikari.ModalInteraction
) -> hikari.Member | None:
    """The member a staff-opened modal is for, or None when the opener is the owner.

    Re-checks `_is_staff` rather than trusting the target id in the custom_id.
    """
    custom_id = interaction.custom_id
    target_id = custom_id.removeprefix(f"{TOPIC_MODAL_ID}:")
    if target_id == custom_id:
        return None
    if not interaction.member or not _is_staff(st, interaction.member):
        logger.warning(
            "On-behalf ticket modal submitted by non-staff", target=target_id
        )
        return None
    guild_id = st.guild_id
    user_id = int(target_id)
    return bot.cache.get_member(guild_id, user_id) or await bot.rest.fetch_member(
        guild_id, user_id
    )


def _modal_topic(interaction: hikari.ModalInteraction) -> str | None:
    return next(
        (
            component.value
            for row in interaction.components
            for component in row.components
            if component.custom_id == TOPIC_INPUT_ID
        ),
        None,
    )


async def handle_topic_modal(interaction: hikari.ModalInteraction) -> None:
    """Handle ticket topic modal submission — create channel + ping staff."""
    if not interaction.guild_id or not interaction.member:
        return

    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    gc = GuildContext.from_interaction(interaction)  # type: ignore[arg-type]
    st = tickets_state.load(int(interaction.guild_id))

    topic = _modal_topic(interaction)
    if not topic:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="*confused head tilt* I didn't catch a topic — please try `/adultier-adult` again! 🐉",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    # Defer — channel creation takes a moment
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.DEFERRED_MESSAGE_CREATE,
        flags=hikari.MessageFlag.EPHEMERAL,
    )

    opener = interaction.member
    try:
        owner = await _ticket_owner(bot, st, interaction) or opener
    except hikari.NotFoundError:
        await interaction.edit_initial_response(
            content="*confused head tilt* I can't find that member anymore — maybe they wandered off? 🐾"
        )
        return
    on_behalf = owner.id != opener.id

    # Race guard: check again after defer
    existing = _find_open_ticket(st, owner.id)
    if existing:
        await interaction.edit_initial_response(
            content=f"*happy tail wag* Looks like that ticket just got opened: <#{existing.channel_id}>! 🐾"
        )
        return

    # Pre-flight: verify bot has permission to create the channel
    missing, scope = await _check_create_perms(bot, gc.guild_id, st.category_id)
    if missing:
        missing_str = ", ".join(f"**{m}**" for m in missing)
        gc.logger.error(
            "Cannot create ticket channel — missing permissions",
            scope=scope,
            missing=missing,
        )
        await gc.log(
            f"🤯 *snorts smoke* I tried to open a ticket for **{owner.display_name}** "
            f"but I'm missing {missing_str} in {scope}! Please check my permissions there. 🐉"
        )
        await interaction.edit_initial_response(
            content="*sad smoke puff* I couldn't open a ticket channel — I'm missing permissions. Please let staff know! 🐉"
        )
        return

    channel_name = _sanitize_channel_name(owner.display_name)

    try:
        channel = await gc.create_private_channel(
            channel_name,
            user_ids=[owner.id],
            extra_roles=[st.staff_role_id] if st.staff_role_id else [],
            category_id=st.category_id,
        )
    except hikari.HTTPError:
        await interaction.edit_initial_response(
            content="*sad smoke puff* I couldn't open a ticket channel — looks like I'm missing permissions. Please let staff know! 🐉"
        )
        return

    # Persist ticket — re-load state to capture any concurrent mutations since the initial load
    st = tickets_state.load(int(interaction.guild_id))
    st.open_tickets.append(
        OpenTicket(
            user_id=int(owner.id),
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

    if on_behalf:
        welcome = (
            f"*flaps wings excitedly* Hi <@{owner.id}>! **{opener.display_name}** "
            f"dug out this cozy little den so you can talk with the staff team. 🎫\n\n"
            f"{staff_ping}\n\n"
            f"Topic: **{topic}**"
        )
        reply = f"*happy tail wag* I've opened a ticket for **{owner.display_name}**: <#{channel.id}> 🐉"
        log_line = (
            f"🎫 *happy flap* **{opener.display_name}** asked me to open a cozy little "
            f'ticket for **{owner.display_name}** about: "{topic}" 🐾'
        )
        summary = (
            f"Ticket opened for them by {journal.escape_markdown(opener.display_name)}: "
            f'"{journal.escape_markdown(topic)}"'
        )
    else:
        welcome = (
            f"*flaps wings excitedly* A new ticket has landed! 🎫\n\n"
            f"{staff_ping}\n\n"
            f"**{owner.display_name}** needs help with: **{topic}**"
        )
        reply = f"*happy tail wag* I've opened a cozy little ticket channel for you: <#{channel.id}> — hop on in! 🐉"
        log_line = (
            f"🎫 *happy flap* I just opened a cozy little ticket for "
            f'**{owner.display_name}**! They need help with: "{topic}" 🐾'
        )
        summary = f'Opened a ticket: "{journal.escape_markdown(topic)}"'

    await bot.rest.create_message(
        channel=channel.id,
        content=welcome,
        components=[buttons_row],
        # Explicit allow-lists: hikari suppresses pings by default, and the
        # member-written topic must not be able to ping anyone else.
        user_mentions=[owner.id],
        role_mentions=[st.staff_role_id] if st.staff_role_id else False,
    )

    await interaction.edit_initial_response(content=reply)

    gc.logger.info(
        "Opened ticket",
        channel=channel_name,
        topic=topic,
        owner=owner.display_name,
    )
    await gc.log(
        log_line,
        journal_kind="ticket_opened",
        journal_user=owner,
        journal_summary=summary,
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

    gc = GuildContext.from_interaction(interaction)
    st = tickets_state.load(int(interaction.guild_id))

    ticket = next((t for t in st.open_tickets if t.channel_id == channel_id), None)
    if ticket:
        opener = gc.bot.cache.get_member(interaction.guild_id, ticket.user_id)
        opener_name = opener.display_name if opener else str(ticket.user_id)
    else:
        opener_name = "someone"

    st.open_tickets = [t for t in st.open_tickets if t.channel_id != channel_id]
    tickets_state.save(st)

    await gc.delete_channel(channel_id)

    closer = (
        interaction.member.display_name
        if interaction.member
        else interaction.user.display_name
    )
    gc.logger.info("Closed ticket", channel_id=channel_id)
    await gc.log(
        f"🔒 *nom nom* Ticket for **{opener_name}** got all wrapped up and closed by "
        f"**{closer}** — all tidied away! 🐾"
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

    # Defer before the REST calls below — component interactions get no
    # auto-defer, so slow work first would blow Discord's 3-second deadline.
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.DEFERRED_MESSAGE_CREATE,
        flags=hikari.MessageFlag.EPHEMERAL,
    )

    try:
        await bot.rest.edit_permission_overwrite(
            channel=channel_id,
            target=selected_user_id,
            target_type=hikari.PermissionOverwriteType.MEMBER,
            allow=PRIVATE_CHANNEL_USER_PERMS,
        )
    except hikari.ForbiddenError:
        await interaction.edit_initial_response(
            "*sad smoke puff* I couldn't add them — missing permissions! 🐉"
        )
        return

    await bot.rest.create_message(
        channel=channel_id,
        content=f"*nom* I've let <@{selected_user_id}> into the ticket! 🐾",
    )
    await interaction.edit_initial_response(
        f"Done! <@{selected_user_id}> can now see this ticket 🐉"
    )
