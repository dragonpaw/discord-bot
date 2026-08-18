from __future__ import annotations

import hikari
import lightbulb
import structlog

from dragonpaw_bot import journal
from dragonpaw_bot.context import GuildContext

logger = structlog.get_logger(__name__)

loader = lightbulb.Loader()
journal_group = lightbulb.Group("journal", "Member journal")


def staff_blocked(ctx: lightbulb.Context, staff_role_id: int | None) -> str | None:
    """Return a refusal message if the caller may not use the journal."""
    if staff_role_id is None:
        return (
            "*tilts head* I don't have a staff role set up yet, so I can't tell "
            "who's allowed to peek at my journal! Ask an admin to run "
            "`/config journal set` first. 🐉"
        )
    if not ctx.member or staff_role_id not in {int(r) for r in ctx.member.role_ids}:
        return "*curls protectively around my journal* Sorry, this one's staff-only! 🐉"
    return None


ADD_MODAL_PREFIX = "journal_add_modal:"
FOLLOWUP_MODAL_PREFIX = "journal_followup_modal:"
WARN_MSG_MODAL_PREFIX = "journal_warn_msg_modal:"
REASON_FIELD = "reason"
EVIDENCE_FIELD = "evidence"

#: Discord's text input cap.
_EVIDENCE_LIMIT = 4000

#: An authored entry's summary is its reason on one line; detail.reason keeps the rest.
SUMMARY_LIMIT = 200

_AUTHORED_CHOICES = [
    lightbulb.Choice("warning", "warning"),
    lightbulb.Choice("note", "note"),
    lightbulb.Choice("ineligible (un-invite from parties)", "ineligible"),
    lightbulb.Choice("eligible (re-invite to parties)", "eligible"),
]

#: Discord's embed description cap; leave room for the truncation notice.
_DESCRIPTION_LIMIT = 4096
_TRUNCATION_NOTICE = "\n*… older entries omitted.*"


def render_entry(entry: journal.JournalEntry) -> str:
    """One entry as display text, with its follow-ups nested beneath it."""
    emoji = journal.KIND_EMOJI.get(entry.kind, "•")
    date = entry.created_at.strftime("%Y-%m-%d")
    line = f"{emoji} `#{entry.id}` **{date}** — {entry.summary}"

    if entry.detail:
        line += f" *(by {entry.detail.issuer_name})*"
        if entry.detail.evidence_url:
            line += f" · [context]({entry.detail.evidence_url})"

    lines = [line]
    if entry.detail:
        lines.extend(
            f"　↳ *{f.created_at.strftime('%Y-%m-%d')} {f.author_name}:* {f.text}"
            for f in entry.detail.follow_ups
        )
    return "\n".join(lines)


def render_timeline(entries: list[journal.JournalEntry]) -> str:
    """Entries as one description block, newest first, capped to Discord's limit."""
    if not entries:
        return "*wags tail* Nothing in my journal for them at all! 🐉"

    rendered: list[str] = []
    length = 0
    for entry in entries:
        block = render_entry(entry)
        if length + len(block) + 1 > _DESCRIPTION_LIMIT - len(_TRUNCATION_NOTICE):
            return "\n".join(rendered) + _TRUNCATION_NOTICE
        rendered.append(block)
        length += len(block) + 1
    return "\n".join(rendered)


@journal_group.register
class JournalView(
    lightbulb.SlashCommand,
    name="view",
    description="Show a member's full journal history.",
):
    user = lightbulb.user("user", "The member to look up")

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        guild_id = int(ctx.guild_id)
        st = journal.load(guild_id)

        if refusal := staff_blocked(ctx, st.staff_role_id):
            logger.info("Journal view denied", actor=ctx.user.username)
            await ctx.respond(refusal, flags=hikari.MessageFlag.EPHEMERAL)
            return

        user_id = int(self.user.id)
        entries = journal.entries_for(guild_id, user_id)

        embed = hikari.Embed(
            title=f"📖 Journal — {self.user.display_name}",
            description=render_timeline(entries),
        )
        if journal.is_ineligible(guild_id, user_id):
            embed.add_field(
                name="🚫 Currently ineligible",
                value="They're off the graduation party list right now.",
            )

        gc = GuildContext.from_ctx(ctx)
        gc.logger.info(
            "Journal viewed", target=self.user.display_name, entries=len(entries)
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


@journal_group.register
class JournalIneligibleList(
    lightbulb.SlashCommand,
    name="ineligible-list",
    description="List everyone currently off the graduation party list.",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        guild_id = int(ctx.guild_id)
        st = journal.load(guild_id)

        if refusal := staff_blocked(ctx, st.staff_role_id):
            logger.info("Journal ineligible-list denied", actor=ctx.user.username)
            await ctx.respond(refusal, flags=hikari.MessageFlag.EPHEMERAL)
            return

        user_ids = journal.ineligible_user_ids(guild_id)
        if not user_ids:
            await ctx.respond(
                "*happy tail wag* Everyone's on the list — nobody's sitting out! 🐉",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        lines = []
        for uid in user_ids:
            latest = next(
                e for e in journal.entries_for(guild_id, uid) if e.kind == "ineligible"
            )
            date = latest.created_at.strftime("%Y-%m-%d")
            lines.append(f"🚫 <@{uid}> — *{date}* — {latest.summary}")

        await ctx.respond(
            embed=hikari.Embed(
                title="🚫 Currently ineligible", description="\n".join(lines)
            ),
            flags=hikari.MessageFlag.EPHEMERAL,
        )


def summarize(reason: str) -> str:
    """One-line form of a reason, for the timeline."""
    flat = " ".join(reason.split())
    if len(flat) <= SUMMARY_LIMIT:
        return flat
    return flat[: SUMMARY_LIMIT - 1] + "…"


def reason_modal_rows(label: str) -> list[hikari.impl.ModalActionRowBuilder]:
    row = hikari.impl.ModalActionRowBuilder()
    row.add_text_input(
        REASON_FIELD,
        label,
        style=hikari.TextInputStyle.PARAGRAPH,
        required=True,
        min_length=1,
        max_length=2000,
    )
    return [row]


def modal_value(interaction: hikari.ModalInteraction, field: str) -> str:
    for row in interaction.components:
        for component in row.components:
            if component.custom_id == field:
                return component.value or ""
    return ""


@journal_group.register
class JournalAdd(
    lightbulb.SlashCommand,
    name="add",
    description="File a note, warning, or eligibility change for a member.",
):
    user = lightbulb.user("user", "The member this is about")
    kind = lightbulb.string("kind", "What kind of entry", choices=_AUTHORED_CHOICES)

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        st = journal.load(int(ctx.guild_id))

        if refusal := staff_blocked(ctx, st.staff_role_id):
            logger.info("Journal add denied", actor=ctx.user.username)
            await ctx.respond(refusal, flags=hikari.MessageFlag.EPHEMERAL)
            return

        await ctx.respond_with_modal(
            title=f"Journal entry — {self.user.display_name}",
            custom_id=f"{ADD_MODAL_PREFIX}{int(self.user.id)}:{self.kind}",
            components=reason_modal_rows("What happened?"),
        )


async def handle_add_modal(interaction: hikari.ModalInteraction) -> None:
    """Persist a staff-authored entry submitted from /journal add."""
    if not interaction.guild_id or not interaction.member:
        return

    await interaction.create_initial_response(
        response_type=hikari.ResponseType.DEFERRED_MESSAGE_CREATE,
        flags=hikari.MessageFlag.EPHEMERAL,
    )

    _, user_id_raw, kind = interaction.custom_id.split(":", 2)
    user_id = int(user_id_raw)
    reason = modal_value(interaction, REASON_FIELD)

    gc = GuildContext.from_interaction(interaction)  # type: ignore[arg-type]
    member = gc.bot.cache.get_member(interaction.guild_id, user_id)
    target_name = member.display_name if member else str(user_id)

    entry = journal.record(
        int(interaction.guild_id),
        gc.name,
        user_id=user_id,
        user_name=target_name,
        kind=kind,  # type: ignore[arg-type]
        summary=summarize(reason),
        detail=journal.WarningDetail(
            reason=reason,
            issuer_id=int(interaction.member.id),
            issuer_name=interaction.member.display_name,
        ),
    )

    emoji = journal.KIND_EMOJI.get(kind, "📖")
    logger.info("Journal entry filed", kind=kind, target=target_name, entry_id=entry.id)
    await interaction.edit_initial_response(
        content=f"{emoji} Noted it down as `#{entry.id}` — I never forget! 🐉"
    )
    await gc.log(
        f"{emoji} **{interaction.member.display_name}** filed a {kind} for "
        f"**{target_name}** as `#{entry.id}` 🐾"
    )


@journal_group.register
class JournalFollowup(
    lightbulb.SlashCommand,
    name="followup",
    description="Append a follow-up to an existing entry (nothing is ever edited).",
):
    entry_id = lightbulb.integer("entry_id", "The `#id` shown in /journal view")

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        st = journal.load(int(ctx.guild_id))

        if refusal := staff_blocked(ctx, st.staff_role_id):
            logger.info("Journal followup denied", actor=ctx.user.username)
            await ctx.respond(refusal, flags=hikari.MessageFlag.EPHEMERAL)
            return

        entry = journal.entry_by_id(int(ctx.guild_id), self.entry_id)
        if entry is None or entry.detail is None:
            await ctx.respond(
                f"*sniffs around* I can't find a staff-filed entry `#{self.entry_id}` "
                f"to add to! Follow-ups only go on notes and warnings. 🐉",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        await ctx.respond_with_modal(
            title=f"Follow-up on #{self.entry_id}",
            custom_id=f"{FOLLOWUP_MODAL_PREFIX}{self.entry_id}",
            components=reason_modal_rows("What's the update?"),
        )


async def handle_followup_modal(interaction: hikari.ModalInteraction) -> None:
    """Append a follow-up submitted from /journal followup."""
    if not interaction.guild_id or not interaction.member:
        return

    await interaction.create_initial_response(
        response_type=hikari.ResponseType.DEFERRED_MESSAGE_CREATE,
        flags=hikari.MessageFlag.EPHEMERAL,
    )

    entry_id = int(interaction.custom_id.removeprefix(FOLLOWUP_MODAL_PREFIX))
    text = modal_value(interaction, REASON_FIELD)
    gc = GuildContext.from_interaction(interaction)  # type: ignore[arg-type]

    entry = journal.add_follow_up(
        int(interaction.guild_id),
        entry_id,
        author_id=int(interaction.member.id),
        author_name=interaction.member.display_name,
        text=text,
    )
    if entry is None:
        await interaction.edit_initial_response(
            content=f"*tilts head* Entry `#{entry_id}` slipped away on me! 🐉"
        )
        return

    await interaction.edit_initial_response(
        content=f"💬 Added your follow-up to `#{entry_id}` 🐉"
    )
    await gc.log(
        f"💬 **{interaction.member.display_name}** added a follow-up to "
        f"`#{entry_id}` (**{entry.user_name}**) 🐾"
    )


def jump_url(guild_id: int, channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


@loader.command
class LogWarningMessageCommand(
    lightbulb.MessageCommand,
    name="Log warning",
    description="File a warning about this message.",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        st = journal.load(int(ctx.guild_id))

        if refusal := staff_blocked(ctx, st.staff_role_id):
            logger.info("Journal message-command denied", actor=ctx.user.username)
            await ctx.respond(refusal, flags=hikari.MessageFlag.EPHEMERAL)
            return

        message = self.target

        reason_row = hikari.impl.ModalActionRowBuilder()
        reason_row.add_text_input(
            REASON_FIELD,
            "What's the problem?",
            style=hikari.TextInputStyle.PARAGRAPH,
            required=True,
            min_length=1,
            max_length=2000,
        )
        # Pre-filled rather than re-fetched on submit: capturing the text before
        # it can be deleted is the whole point of the context menu.
        evidence_row = hikari.impl.ModalActionRowBuilder()
        evidence_row.add_text_input(
            EVIDENCE_FIELD,
            "Message being cited (edit to trim)",
            style=hikari.TextInputStyle.PARAGRAPH,
            required=False,
            max_length=_EVIDENCE_LIMIT,
            value=(message.content or "")[:_EVIDENCE_LIMIT],
        )

        await ctx.respond_with_modal(
            title=f"Warning — {message.author.username}",
            custom_id=(
                f"{WARN_MSG_MODAL_PREFIX}{int(message.author.id)}:"
                f"{int(message.channel_id)}:{int(message.id)}"
            ),
            components=[reason_row, evidence_row],
        )


async def handle_warn_message_modal(interaction: hikari.ModalInteraction) -> None:
    """Persist a warning filed from the message context menu."""
    if not interaction.guild_id or not interaction.member:
        return

    await interaction.create_initial_response(
        response_type=hikari.ResponseType.DEFERRED_MESSAGE_CREATE,
        flags=hikari.MessageFlag.EPHEMERAL,
    )

    _, user_raw, channel_raw, message_raw = interaction.custom_id.split(":", 3)
    user_id = int(user_raw)
    reason = modal_value(interaction, REASON_FIELD)
    evidence = modal_value(interaction, EVIDENCE_FIELD)

    gc = GuildContext.from_interaction(interaction)  # type: ignore[arg-type]
    guild_id = int(interaction.guild_id)
    member = gc.bot.cache.get_member(guild_id, user_id)
    target_name = member.display_name if member else str(user_id)
    url = jump_url(guild_id, int(channel_raw), int(message_raw))

    entry = journal.record(
        guild_id,
        gc.name,
        user_id=user_id,
        user_name=target_name,
        kind="warning",
        summary=summarize(reason),
        detail=journal.WarningDetail(
            reason=reason,
            issuer_id=int(interaction.member.id),
            issuer_name=interaction.member.display_name,
            evidence_url=url,
            evidence_text=evidence or None,
        ),
    )

    logger.info(
        "Journal warning filed from message", target=target_name, entry_id=entry.id
    )
    await interaction.edit_initial_response(
        content=f"⚠️ Filed it as `#{entry.id}`, message and all — I never forget! 🐉"
    )
    await gc.log(
        f"⚠️ **{interaction.member.display_name}** filed a warning for "
        f"**{target_name}** as `#{entry.id}` ([context]({url})) 🐾"
    )


loader.command(journal_group)
