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


loader.command(journal_group)
