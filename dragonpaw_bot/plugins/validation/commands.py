from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import hikari
import hikari.impl
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.validation import state as validation_state
from dragonpaw_bot.plugins.validation.models import ValidationMember, ValidationStage

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

loader = lightbulb.Loader()

APPROVE_BUTTON_PREFIX = "validation_approve:"
APPROVE_MODAL_PREFIX = "validation_approve_modal:"
RULES_AGREED_PREFIX = "validation_rules_agreed:"

ASSETS_DIR = Path(__file__).parent / "assets"
MIN_PHOTOS = 2
SAMPLE_ID_PATH = ASSETS_DIR / "validation-id.jpg"
SAMPLE_SELFIE_PATH = ASSETS_DIR / "validation-selfie.jpg"
CHANNEL_CLOSE_DELAY = 30


async def _close_validate_channel(
    gc: GuildContext, channel_id: int, notice: str
) -> None:
    """Post a closing notice in the validate channel, wait 30s, then delete it."""
    try:
        await gc.bot.rest.create_message(channel=channel_id, content=notice)
    except hikari.NotFoundError:
        gc.logger.debug(
            "Validate channel already gone, skipping close notice",
            channel_id=channel_id,
        )
        return
    except hikari.ForbiddenError:
        gc.logger.warning(
            "Cannot send close notice — missing Send Messages permission",
            channel_id=channel_id,
        )
    except hikari.HTTPError as exc:
        gc.logger.warning(
            "Failed to send close notice in validate channel",
            channel_id=channel_id,
            error=str(exc),
        )
    await asyncio.sleep(CHANNEL_CLOSE_DELAY)
    await gc.delete_channel(channel_id)


def _sanitize_channel_name(display_name: str) -> str:
    """Convert a display name to a valid Discord channel name: validate-{name}."""
    name = re.sub(r"[\[({][^\[({]*[\])}].*$", "", display_name).strip()
    name = name.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name)
    name = name.strip("-")
    if not name:
        name = "member"
    return f"validate-{name}"[:100]


# ---------------------------------------------------------------------------- #
#                              Event listeners                                  #
# ---------------------------------------------------------------------------- #


@loader.listener(hikari.MemberCreateEvent)
async def on_member_join(event: hikari.MemberCreateEvent) -> None:
    """Add new member to onboarding flow and post lobby welcome."""
    bot: DragonpawBot = event.app  # type: ignore[assignment]
    st = validation_state.load(int(event.guild_id))

    if event.member.is_bot:
        gc = GuildContext.from_guild(
            bot,
            bot.cache.get_guild(event.guild_id)
            or await bot.rest.fetch_guild(event.guild_id),
        )
        await gc.log(f"🤖 Bot joined: {event.member.mention} — skipping onboarding 🐉")
        return

    if not st.lobby_channel_id:
        logger.debug(
            "No lobby channel configured, skipping welcome", guild_id=event.guild_id
        )
        return

    st.members.append(
        ValidationMember(
            user_id=int(event.member.id),
            joined_at=datetime.now(UTC),
        )
    )
    validation_state.save(st)

    row = bot.rest.build_message_action_row()
    row.add_interactive_button(
        hikari.ButtonStyle.SUCCESS,
        f"{RULES_AGREED_PREFIX}{event.member.id}",
        label="I've read the rules! ✅",
    )

    try:
        await bot.rest.create_message(
            channel=st.lobby_channel_id,
            content=(
                f"*flaps wings excitedly* Hiya {event.member.mention}! 🐉 Welcome to the server! "
                f"Before I let you into the hoard proper, I need you to read the rules first"
                + (
                    f" — you can find them in <#{st.about_channel_id}>!"
                    if st.about_channel_id
                    else "!"
                )
                + " Give 'em a good read and then smack that button below! *happy tail wag* 🐾"
            ),
            components=[row],
        )
    except hikari.HTTPError:
        logger.warning("Failed to post lobby welcome", user=event.member.display_name)
        return
    logger.info("Posted lobby welcome", user=event.member.display_name)


@loader.listener(hikari.MemberUpdateEvent)
async def on_member_update(event: hikari.MemberUpdateEvent) -> None:
    """If a member gains the member role while in onboarding, drop them from the flow."""
    if not event.member:
        return
    bot: DragonpawBot = event.app  # type: ignore[assignment]
    st = validation_state.load(int(event.guild_id))

    if not st.member_role_id:
        return

    member_entry = next(
        (m for m in st.members if m.user_id == int(event.member.id)), None
    )
    if not member_entry:
        return

    if hikari.Snowflake(st.member_role_id) not in event.member.role_ids:
        return

    # Check the audit log to find who assigned the role
    actor_id: hikari.Snowflake | None = None
    try:
        audit_log = await bot.rest.fetch_audit_log(
            event.guild_id,
            event_type=hikari.AuditLogEventType.MEMBER_ROLE_UPDATE,
        )
        matching = [
            e
            for page in audit_log
            for e in page.entries.values()
            if e.target_id == event.member.id
        ]
        if matching:
            actor_id = max(matching, key=lambda e: e.id).user_id
    except hikari.HTTPError:
        logger.warning(
            "Failed to fetch audit log for role assignment",
            user=event.member.display_name,
        )

    if actor_id == bot.user_id:
        return  # Bot's own approval — already handled in handle_approve_modal

    # They now have the member role — remove from onboarding
    gc = GuildContext.from_guild(
        bot,
        bot.cache.get_guild(event.guild_id)
        or await bot.rest.fetch_guild(event.guild_id),
    )
    by_whom = f" — role given by <@{actor_id}>" if actor_id else ""
    st.members = [m for m in st.members if m.user_id != int(event.member.id)]
    validation_state.save(st)
    await gc.log(
        f"*happy snort* Dropped {event.member.mention} from onboarding — "
        f"they already have the member role{by_whom}! 🐉"
    )
    logger.info(
        "Dropped member from onboarding (already has member role)",
        user=event.member.display_name,
    )
    if member_entry.channel_id:
        asyncio.get_running_loop().create_task(
            _close_validate_channel(
                gc,
                member_entry.channel_id,
                f"✅ *happy snort* Looks like you've already been added to the hoard{by_whom}! "
                f"This channel will be deleted in {CHANNEL_CLOSE_DELAY} seconds~ 🐉",
            )
        )


@loader.listener(hikari.GuildMessageCreateEvent)
async def on_message_create(event: hikari.GuildMessageCreateEvent) -> None:
    """Count image attachments posted by the member in their validate channel."""
    if event.is_bot:
        return

    st = validation_state.load(int(event.guild_id))
    member_entry = next(
        (
            m
            for m in st.members
            if m.channel_id == int(event.channel_id)
            and m.stage == ValidationStage.AWAITING_PHOTOS
            and m.user_id == int(event.author_id)
        ),
        None,
    )
    if not member_entry:
        return

    image_count = sum(
        1
        for a in event.message.attachments
        if a.media_type and a.media_type.startswith("image/")
    )
    if not image_count:
        return

    member_entry.photo_count += image_count

    if member_entry.photo_count >= MIN_PHOTOS:
        member_entry.stage = ValidationStage.AWAITING_STAFF
        validation_state.save(st)

        bot: DragonpawBot = event.app  # type: ignore[assignment]
        gc = GuildContext.from_guild(
            bot,
            bot.cache.get_guild(event.guild_id)
            or await bot.rest.fetch_guild(event.guild_id),
        )

        staff_ping = f"<@&{st.staff_role_id}>" if st.staff_role_id else "Hey staff!"
        approve_row = bot.rest.build_message_action_row()
        approve_row.add_interactive_button(
            hikari.ButtonStyle.SUCCESS,
            f"{APPROVE_BUTTON_PREFIX}{event.channel_id}",
            label="Looks good! ✅",
        )
        try:
            await bot.rest.create_message(
                channel=event.channel_id,
                content=(
                    f"*sniffs excitedly* 🐉 {staff_ping} — <@{member_entry.user_id}> has submitted "
                    f"their verification photos! Do these look legit?"
                ),
                components=[approve_row],
            )
        except hikari.HTTPError:
            logger.warning("Failed to post staff ping", channel_id=event.channel_id)
        await gc.log(
            f"📸 Photos submitted by <@{member_entry.user_id}> in <#{event.channel_id}> — "
            f"awaiting staff review 👀🐉"
        )
        logger.info(
            "Photos submitted, awaiting staff review", user_id=member_entry.user_id
        )
    else:
        validation_state.save(st)
        logger.debug(
            "Photo counted",
            user_id=member_entry.user_id,
            photo_count=member_entry.photo_count,
        )


@loader.listener(hikari.MemberDeleteEvent)
async def on_member_leave(event: hikari.MemberDeleteEvent) -> None:
    """Clean up state and validate channel when a member leaves mid-onboarding."""
    bot: DragonpawBot = event.app  # type: ignore[assignment]
    st = validation_state.load(int(event.guild_id))

    member_entry = next(
        (m for m in st.members if m.user_id == int(event.user_id)), None
    )
    if not member_entry:
        return

    st.members = [m for m in st.members if m.user_id != int(event.user_id)]
    validation_state.save(st)

    gc = GuildContext.from_guild(
        bot,
        bot.cache.get_guild(event.guild_id)
        or await bot.rest.fetch_guild(event.guild_id),
    )
    display = (
        event.old_member.display_name if event.old_member else str(int(event.user_id))
    )
    await gc.log(
        f"*sad snort* <@{event.user_id}> flew away before finishing onboarding — "
        f"cleaning up! 🐉"
    )
    logger.info("Removed member from onboarding on leave", user=display)

    if member_entry.channel_id:
        asyncio.get_running_loop().create_task(
            _close_validate_channel(
                gc,
                member_entry.channel_id,
                f"*sad snort* Looks like they flew away before finishing! "
                f"This channel will be deleted in {CHANNEL_CLOSE_DELAY} seconds~ 🐉",
            )
        )


# ---------------------------------------------------------------------------- #
#                           Interaction handlers                                #
# ---------------------------------------------------------------------------- #


async def handle_rules_agreed(interaction: hikari.ComponentInteraction) -> None:
    """Handle the 'I've read the rules' button — create private validate channel."""
    if not interaction.guild_id or not interaction.member:
        return

    user_id_str = interaction.custom_id.removeprefix(RULES_AGREED_PREFIX)
    try:
        expected_user_id = int(user_id_str)
    except ValueError:
        return

    if int(interaction.user.id) != expected_user_id:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="*tilts head* That button isn't for you! 🐉",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    await interaction.create_initial_response(
        response_type=hikari.ResponseType.DEFERRED_MESSAGE_CREATE,
        flags=hikari.MessageFlag.EPHEMERAL,
    )

    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    gc = GuildContext.from_interaction(interaction)
    st = validation_state.load(int(interaction.guild_id))

    member_entry = next(
        (m for m in st.members if m.user_id == int(interaction.user.id)), None
    )
    if not member_entry:
        await interaction.edit_initial_response(
            content="*confused head tilt* Hmm, I don't have you in my onboarding list! Please let staff know. 🐉"
        )
        return

    if member_entry.stage != ValidationStage.AWAITING_RULES:
        await interaction.edit_initial_response(
            content=f"*happy tail wag* You've already got a validate channel going: <#{member_entry.channel_id}>! Head on over~ 🐾"
        )
        return

    channel_name = _sanitize_channel_name(interaction.member.display_name)

    try:
        channel = await gc.create_private_channel(
            channel_name,
            user_ids=[interaction.user.id],
            extra_roles=[st.staff_role_id] if st.staff_role_id else [],
            category_id=st.validate_category_id,
        )
    except hikari.HTTPError:
        await interaction.edit_initial_response(
            content="*sad smoke puff* I couldn't open your validate channel — I'm missing permissions. Please let staff know! 🐉"
        )
        return

    member_entry.stage = ValidationStage.AWAITING_PHOTOS
    member_entry.channel_id = int(channel.id)
    validation_state.save(st)

    attachments = []
    if SAMPLE_ID_PATH.exists():
        attachments.append(hikari.File(SAMPLE_ID_PATH))
    if SAMPLE_SELFIE_PATH.exists():
        attachments.append(hikari.File(SAMPLE_SELFIE_PATH))

    try:
        await bot.rest.create_message(
            channel=channel.id,
            content=(
                f"*wiggles tail* Hiya {interaction.user.mention}! 🐉 Thanks for reading the rules — "
                f"you're almost in the hoard!\n\n"
                f"To finish up, I need you to post **two photos** in this channel:\n"
                f"1. 📄 A photo of your **government-issued ID** showing your date of birth\n"
                f"2. 🤳 A **selfie of you holding that same ID**, along with a handwritten note showing "
                f'**"{gc.name}"** and today\'s date (**{interaction.created_at.strftime("%B %d, %Y")}**) '
                f"so we know it's a fresh photo just for us!\n\n"
                f"*I've attached some examples below so you know what I'm looking for!* "
                f"Once you post both, I'll ping staff to take a look. 🐾"
            ),
            attachments=attachments if attachments else hikari.UNDEFINED,
        )
    except hikari.HTTPError:
        logger.warning("Failed to post photo instructions", channel_id=channel.id)

    await interaction.edit_initial_response(
        content=f"*happy flap* I've opened a cozy little validate channel for you: <#{channel.id}> — hop on in! 🐉"
    )

    gc.logger.info("Opened validate channel", channel=channel_name)
    await gc.log(
        f"🆕 *happy flap* Opened validate channel for {interaction.user.mention} "
        f"in <#{channel.id}> 🐉"
    )


def _is_staff(
    interaction: hikari.ComponentInteraction | hikari.ModalInteraction,
    staff_role_id: int | None,
) -> bool:
    """Return True if the interacting member has admin permission or the staff role."""
    if not interaction.member:
        return False
    if interaction.member.permissions & hikari.Permissions.ADMINISTRATOR:
        return True
    if staff_role_id is None:
        return False
    return staff_role_id in {int(r) for r in interaction.member.role_ids}


async def handle_approve_button(interaction: hikari.ComponentInteraction) -> None:
    """Show the name-entry modal when staff clicks 'Looks good!'."""
    if not interaction.guild_id:
        return

    channel_id_str = interaction.custom_id.removeprefix(APPROVE_BUTTON_PREFIX)
    try:
        channel_id = int(channel_id_str)
    except ValueError:
        return

    st = validation_state.load(int(interaction.guild_id))
    member_entry = next((m for m in st.members if m.channel_id == channel_id), None)

    if member_entry and int(interaction.user.id) == member_entry.user_id:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="*side-eyes you* 🐉 You can't approve your own verification! 🐾",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    if not _is_staff(interaction, st.staff_role_id):
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="*snorts smoke* 🐉 Only staff can approve verifications! 🐾",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    name_row = hikari.impl.ModalActionRowBuilder()
    name_row.add_text_input(
        "validation_name_input",
        "What name should they go by?",
        placeholder="Enter their approved display name",
        required=True,
        min_length=1,
        max_length=32,
    )
    await interaction.create_modal_response(
        title="*nom nom* Approve this member 🐉",
        custom_id=f"{APPROVE_MODAL_PREFIX}{channel_id_str}",
        components=[name_row],
    )


async def handle_approve_modal(interaction: hikari.ModalInteraction) -> None:  # noqa: PLR0912, PLR0915
    """Approve the member: set nickname, assign role, announce, close channel."""
    if not interaction.guild_id or not interaction.member:
        return

    await interaction.create_initial_response(
        response_type=hikari.ResponseType.DEFERRED_MESSAGE_CREATE,
        flags=hikari.MessageFlag.EPHEMERAL,
    )

    channel_id_str = interaction.custom_id.removeprefix(APPROVE_MODAL_PREFIX)
    try:
        channel_id = int(channel_id_str)
    except ValueError:
        await interaction.edit_initial_response(
            content="*confused head tilt* Something went wrong parsing that request — please try again! 🐉"
        )
        return

    st = validation_state.load(int(interaction.guild_id))
    member_entry_check = next(
        (m for m in st.members if m.channel_id == channel_id), None
    )
    if member_entry_check and int(interaction.user.id) == member_entry_check.user_id:
        await interaction.edit_initial_response(
            content="*side-eyes you* 🐉 You can't approve your own verification! 🐾"
        )
        return
    if not _is_staff(interaction, st.staff_role_id):
        await interaction.edit_initial_response(
            content="*snorts smoke* 🐉 Only staff can approve verifications! 🐾"
        )
        return

    name: str | None = None
    for row in interaction.components:
        for component in row.components:
            if component.custom_id == "validation_name_input":
                name = component.value.strip()
    if not name:
        await interaction.edit_initial_response(
            content="*confused head tilt* I didn't catch a name — please try again! 🐉"
        )
        return

    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    gc = GuildContext.from_interaction(interaction)  # type: ignore[arg-type]

    member_entry = member_entry_check
    if not member_entry:
        await interaction.edit_initial_response(
            content="*confused head tilt* I couldn't find that validation entry — it may have already been processed. 🐉"
        )
        return

    user_id = hikari.Snowflake(member_entry.user_id)

    st.members = [m for m in st.members if m.channel_id != channel_id]
    validation_state.save(st)

    try:
        await bot.rest.edit_member(interaction.guild_id, user_id, nickname=name)
    except hikari.ForbiddenError:
        gc.logger.warning("Cannot set nickname — missing permissions", user_id=user_id)
        await gc.log(
            f"⚠️ I couldn't set the nickname for <@{user_id}> — "
            f"please check my **Manage Nicknames** permission! 🐉"
        )
    except hikari.HTTPError:
        gc.logger.exception("Failed to set nickname", user_id=user_id)
        await gc.log(
            f"⚠️ Something went wrong setting the nickname for <@{user_id}> — check the logs! 🐉"
        )

    if st.member_role_id:
        try:
            await bot.rest.add_role_to_member(
                interaction.guild_id, user_id, hikari.Snowflake(st.member_role_id)
            )
        except hikari.ForbiddenError:
            gc.logger.warning(
                "Cannot assign member role — missing permissions", user_id=user_id
            )
            await gc.log(
                f"⚠️ I couldn't assign the member role to <@{user_id}> — "
                f"please check my **Manage Roles** permission and role hierarchy! 🐉"
            )
        except hikari.HTTPError:
            gc.logger.exception("Failed to assign member role", user_id=user_id)
            await gc.log(
                f"⚠️ Something went wrong assigning the member role to <@{user_id}> — check the logs! 🐉"
            )

    bot_st = bot.state(interaction.guild_id)
    general_channel_id = bot_st.general_channel_id if bot_st else None
    if general_channel_id:
        if st.about_channel_id:
            about_ref = f"<#{st.about_channel_id}>"
        else:
            about_ref = "#about"
        if st.roles_channel_id:
            roles_ref = f"<#{st.roles_channel_id}>"
        else:
            roles_ref = "#roles"
        if st.intros_channel_id:
            intros_ref = f"<#{st.intros_channel_id}>"
        else:
            intros_ref = "#introductions"
        if st.events_channel_id:
            events_ref = f"<#{st.events_channel_id}>"
        else:
            events_ref = "#classes-and-events"
        if st.chat_channel_id:
            chat_ref = f"<#{st.chat_channel_id}>"
        else:
            chat_ref = "#general-often-lewd"
        try:
            await bot.rest.create_message(
                channel=general_channel_id,
                content=(
                    f"🎉 *does a happy little dragon wiggle* Everyone say hello to <@{user_id}>! "
                    f"They're officially part of the hoard now~ 🐉\n\n"
                    f"<@{user_id}>, welcome welcome welcome!! A few things to get you settled in:\n"
                    f"• Peek at {about_ref} to learn more about us 📖\n"
                    f"• I'll see you over in {roles_ref} to help pick out your roles — grab some shiny ones! ✨\n"
                    f"• Tell us a little about yourself in {intros_ref} 🐾\n"
                    f"• We host classes and have a SubDay Journal program — check out {events_ref}, or run `/subday about` to learn more! 📚\n"
                    f"• One tiny thing! I have a *very* hungry tummy for text in the media channels 🍽️ "
                    f"*nom nom* Images and links are yummy, but please pop your comments over in {chat_ref}~ 💜\n\n"
                    f"Also — we'd love to know: **how did you find out about OGL?** Drop it in the chat! 🐾"
                ),
            )
        except hikari.HTTPError:
            gc.logger.warning(
                "Failed to post welcome announcement",
                channel_id=int(general_channel_id),
            )
            await gc.log(
                f"⚠️ Couldn't post the welcome announcement for **{name}** in <#{general_channel_id}>! 🐉"
            )

    await gc.log(
        f"✅ *happy flap* Approved <@{user_id}> as **{name}** — "
        f"stamped by {interaction.user.mention}! 🎉🐉"
    )
    gc.logger.info(
        "Member approved",
        user_id=user_id,
        name=name,
        approved_by=interaction.member.display_name,
    )

    await interaction.edit_initial_response(
        content=f"*happy tail wag* Done! **{name}** is in the hoard! 🐉🎉"
    )
    asyncio.get_running_loop().create_task(
        _close_validate_channel(
            gc,
            channel_id,
            f"✅ *happy tail wag* You've been approved and welcomed to the hoard as **{name}**! "
            f"This channel will be deleted in {CHANNEL_CLOSE_DELAY} seconds — see you on the other side! 🐉🎉",
        )
    )
