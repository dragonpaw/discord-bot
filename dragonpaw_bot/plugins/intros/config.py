"""Slash commands: /config intros set|clear"""

from __future__ import annotations

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import (
    CHANNEL_CLEANUP_PERMS,
    GuildContext,
    check_channel_perms,
    guild_owner_only,
)
from dragonpaw_bot.plugins.intros import state as intros_state

logger = structlog.get_logger(__name__)


def register(intros_sub: lightbulb.SubGroup) -> None:
    intros_sub.register(IntrosSet)
    intros_sub.register(IntrosClear)


class IntrosSet(
    lightbulb.SlashCommand,
    name="set",
    description="Configure the introductions channel.",
    hooks=[guild_owner_only],
):
    channel = lightbulb.channel("channel", "The introductions channel")
    role = lightbulb.role(
        "role",
        "Only members with this role appear in /intros missing (optional)",
        default=None,
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        gc = GuildContext.from_ctx(ctx)

        st = intros_state.load(int(ctx.guild_id))
        st.guild_name = gc.name
        st.channel_id = int(self.channel.id)
        st.channel_name = self.channel.name or str(self.channel.id)

        if self.role is not None:
            st.required_role_id = int(self.role.id)
            st.required_role_name = self.role.name
        else:
            st.required_role_id = None
            st.required_role_name = ""

        intros_state.save(st)

        gc.logger.info(
            "Configured intros channel",
            channel=self.channel.name,
            required_role=self.role.name if self.role else None,
        )

        missing = await check_channel_perms(
            gc.bot, ctx.guild_id, self.channel.id, CHANNEL_CLEANUP_PERMS
        )
        warning = ""
        if missing:
            warning = (
                f"\n⚠️ I'm missing permissions in that channel: "
                f"**{', '.join(missing)}**. The daily cleanup won't work until that's fixed."
            )

        role_line = f" Required role: **{self.role.name}**." if self.role else ""
        await ctx.respond(
            f"Introductions channel set to <#{self.channel.id}>.{role_line}{warning}",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        await gc.log(
            f"📋 {ctx.user.mention} pointed me at <#{self.channel.id}> as the intros channel!"
            + (
                f" I'll only watch for members with the **{self.role.name}** role. 🐾"
                if self.role
                else " 🐾"
            ),
        )


class IntrosClear(
    lightbulb.SlashCommand,
    name="clear",
    description="Remove introductions channel configuration.",
    hooks=[guild_owner_only],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        gc = GuildContext.from_ctx(ctx)

        st = intros_state.load(int(ctx.guild_id))
        if st.channel_id is None:
            await ctx.respond(
                "No introductions channel is configured.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        st.guild_name = gc.name
        st.channel_id = None
        st.channel_name = ""
        st.required_role_id = None
        st.required_role_name = ""
        intros_state.save(st)

        gc.logger.info("Cleared intros config")
        await ctx.respond(
            "Introductions channel configuration cleared.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        await gc.log(
            f"📋 {ctx.user.mention} cleared the intros channel config — I'll stop keeping tabs on hellos! 🐉"
        )
