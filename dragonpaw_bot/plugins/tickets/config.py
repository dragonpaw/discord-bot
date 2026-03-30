from __future__ import annotations

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext, check_channel_perms, guild_owner_only
from dragonpaw_bot.plugins.tickets import state as tickets_state

logger = structlog.get_logger(__name__)


def register(sub: lightbulb.SubGroup) -> None:
    """Register /config tickets subcommands."""
    sub.register(TicketsSet)
    sub.register(TicketsStatus)
    sub.register(TicketsClear)


class TicketsSet(
    lightbulb.SlashCommand,
    name="set",
    description="Configure the help ticketing system.",
    hooks=[guild_owner_only],
):
    category = lightbulb.channel(
        "category",
        "Category to create ticket channels under",
        default=None,
        channel_types=[hikari.ChannelType.GUILD_CATEGORY],
    )
    staff_role = lightbulb.role(
        "staff_role", "Role to ping and grant access to tickets", default=None
    )
    required_role = lightbulb.role(
        "required_role", "Role a user must have to open a ticket", default=None
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        gc = GuildContext.from_ctx(ctx)
        st = tickets_state.load(int(ctx.guild_id))
        st.guild_name = gc.name

        if self.category is not None:
            st.category_id = int(self.category.id)
        if self.staff_role is not None:
            st.staff_role_id = int(self.staff_role.id)
        if self.required_role is not None:
            st.required_role_id = int(self.required_role.id)

        tickets_state.save(st)
        gc.logger.info(
            "Configured tickets",
            category=self.category.name if self.category else None,
            staff_role=self.staff_role.name if self.staff_role else None,
            required_role=self.required_role.name if self.required_role else None,
        )

        parts = []
        if self.category:
            parts.append(f"category: <#{self.category.id}>")
        if self.staff_role:
            parts.append(f"staff role: <@&{self.staff_role.id}>")
        if self.required_role:
            parts.append(f"required role: <@&{self.required_role.id}>")

        summary = ", ".join(parts) if parts else "no changes"

        # If a category was configured, check bot has MANAGE_CHANNELS there
        warning = ""
        if self.category is not None:
            missing = await check_channel_perms(
                gc.bot,
                ctx.guild_id,
                self.category.id,
                {hikari.Permissions.MANAGE_CHANNELS: "Manage Channels"},
            )
            if missing:
                warning = "\n⚠️ I'm missing **Manage Channels** in that category — I won't be able to create or delete ticket channels!"

        await ctx.respond(
            f"*happy tail wag* 🐉 Ticket settings updated — {summary}!{warning}",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        await gc.log(f"⚙️ {ctx.user.mention} updated ticket settings — {summary} 🐾")


class TicketsStatus(
    lightbulb.SlashCommand,
    name="status",
    description="Show current help ticket configuration.",
    hooks=[guild_owner_only],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        st = tickets_state.load(int(ctx.guild_id))

        lines = ["*peers around curiously* 🐉 Here's my ticket setup:"]
        lines.append(
            f"• Category: {f'<#{st.category_id}>' if st.category_id else 'not set'}"
        )
        lines.append(
            f"• Staff role: {f'<@&{st.staff_role_id}>' if st.staff_role_id else 'not set'}"
        )
        lines.append(
            f"• Required role: {f'<@&{st.required_role_id}>' if st.required_role_id else 'not set (anyone can open)'}"
        )
        lines.append(f"• Open tickets: {len(st.open_tickets)}")

        await ctx.respond("\n".join(lines), flags=hikari.MessageFlag.EPHEMERAL)


class TicketsClear(
    lightbulb.SlashCommand,
    name="clear",
    description="Clear all help ticket configuration (does not close open tickets).",
    hooks=[guild_owner_only],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        gc = GuildContext.from_ctx(ctx)
        st = tickets_state.load(int(ctx.guild_id))

        st.category_id = None
        st.staff_role_id = None
        st.required_role_id = None
        tickets_state.save(st)

        gc.logger.info("Cleared ticket config")
        await ctx.respond(
            f"*snorts smoke* Ticket configuration cleared! "
            f"Note: {len(st.open_tickets)} open ticket(s) are unaffected 🐉",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        await gc.log(f"⚙️ {ctx.user.mention} cleared ticket configuration 🐾")
