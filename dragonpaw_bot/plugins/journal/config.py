from __future__ import annotations

import hikari
import lightbulb
import structlog

from dragonpaw_bot import journal
from dragonpaw_bot.context import GuildContext, guild_owner_only

logger = structlog.get_logger(__name__)


def register(sub: lightbulb.SubGroup) -> None:
    """Register /config journal subcommands."""
    sub.register(JournalStatus)


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
        staff_role_id = GuildContext.from_ctx(ctx).staff_role_id
        ineligible = journal.ineligible_user_ids(int(ctx.guild_id))
        lines = [
            "*peers into my journal* 🐉 Here's how it's set up:",
            f"• Staff role: {f'<@&{staff_role_id}>' if staff_role_id else 'not set'}"
            " (set with `/config common`)",
            f"• Entries recorded: {len(st.entries)}",
            f"• Currently ineligible: {len(ineligible)}",
        ]
        await ctx.respond("\n".join(lines), flags=hikari.MessageFlag.EPHEMERAL)
