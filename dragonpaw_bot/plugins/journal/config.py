from __future__ import annotations

import hikari
import lightbulb
import structlog

from dragonpaw_bot import journal
from dragonpaw_bot.context import GuildContext, actor_name, guild_owner_only

logger = structlog.get_logger(__name__)


def register(sub: lightbulb.SubGroup) -> None:
    """Register /config journal subcommands."""
    sub.register(JournalSet)
    sub.register(JournalStatus)
    sub.register(JournalClear)


class JournalSet(
    lightbulb.SlashCommand,
    name="set",
    description="Configure the member journal.",
    hooks=[guild_owner_only],
):
    staff_role = lightbulb.role(
        "staff_role", "Role allowed to read and write the journal"
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        gc = GuildContext.from_ctx(ctx)
        st = journal.load(int(ctx.guild_id))
        st.guild_name = gc.name
        st.staff_role_id = int(self.staff_role.id)
        journal.save(st)

        gc.logger.info("Configured journal", staff_role=self.staff_role.name)
        await ctx.respond(
            f"*happy tail wag* 🐉 Journal staff role set to <@&{self.staff_role.id}>!",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        actor = actor_name(ctx)
        await gc.log(
            f"⚙️ **{actor}** set the journal staff role to <@&{self.staff_role.id}> 🐾"
        )


class JournalStatus(
    lightbulb.SlashCommand,
    name="status",
    description="Show current journal configuration.",
    hooks=[guild_owner_only],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        st = journal.load(int(ctx.guild_id))
        ineligible = journal.ineligible_user_ids(int(ctx.guild_id))
        lines = [
            "*peers into my journal* 🐉 Here's how it's set up:",
            f"• Staff role: {f'<@&{st.staff_role_id}>' if st.staff_role_id else 'not set'}",
            f"• Entries recorded: {len(st.entries)}",
            f"• Currently ineligible: {len(ineligible)}",
        ]
        await ctx.respond("\n".join(lines), flags=hikari.MessageFlag.EPHEMERAL)


class JournalClear(
    lightbulb.SlashCommand,
    name="clear",
    description="Clear journal configuration (entries are never deleted).",
    hooks=[guild_owner_only],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        gc = GuildContext.from_ctx(ctx)
        st = journal.load(int(ctx.guild_id))
        st.staff_role_id = None
        journal.save(st)

        gc.logger.info("Cleared journal config")
        await ctx.respond(
            f"*snorts smoke* Journal configuration cleared! "
            f"My {len(st.entries)} entries are all still safe — I never forget. 🐉",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        actor = actor_name(ctx)
        await gc.log(f"⚙️ **{actor}** cleared the journal configuration 🐾")
