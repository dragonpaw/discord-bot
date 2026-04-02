"""Slash commands: /intros missing"""

from __future__ import annotations

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.intros import state as intros_state

logger = structlog.get_logger(__name__)
loader = lightbulb.Loader()

_intros_group = lightbulb.Group("intros", "Introductions channel tools")


class IntrosMissing(
    lightbulb.SlashCommand,
    name="missing",
    description="List members who haven't posted in the introductions channel.",
    hooks=[lightbulb.prefab.has_permissions(hikari.Permissions.MANAGE_GUILD)],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        gc = GuildContext.from_ctx(ctx)

        st = intros_state.load(int(ctx.guild_id))
        if st.channel_id is None:
            await ctx.respond(
                "No introductions channel configured. Use `/config intros set` first.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        response_id = await ctx.respond(
            "Sniffing the intros channel... 🐾",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        # Collect user IDs who have posted in the intros channel (skip bots and pinned)
        posted_ids: set[int] = set()
        async for message in gc.bot.rest.fetch_messages(st.channel_id):
            if not message.author.is_bot and not message.is_pinned:
                posted_ids.add(int(message.author.id))

        # Collect eligible members (non-bot, with required role if configured)
        missing_members: list[hikari.Member] = []
        async for member in gc.bot.rest.fetch_members(ctx.guild_id):
            if member.is_bot:
                continue
            if st.required_role_id is not None and st.required_role_id not in [
                int(r) for r in member.role_ids
            ]:
                continue
            if int(member.id) not in posted_ids:
                missing_members.append(member)

        gc.logger.info(
            "Intros missing check",
            channel=st.channel_name,
            missing_count=len(missing_members),
        )

        if not missing_members:
            role_note = (
                f" (with role **{st.required_role_name}**)"
                if st.required_role_id
                else ""
            )
            await ctx.edit_response(
                response_id,
                content=f"Everyone{role_note} has posted an intro! 🐉",
            )
            return

        mentions = " ".join(m.mention for m in missing_members)
        role_note = (
            f" with role **{st.required_role_name}**" if st.required_role_id else ""
        )
        header = f"**{len(missing_members)} member(s){role_note} haven't posted an intro yet:**\n"

        await ctx.edit_response(response_id, content=header + mentions)
        await gc.log(
            f"👀 {ctx.user.mention} asked who's been shy — "
            f"**{len(missing_members)}** member(s) still haven't introduced themselves in <#{st.channel_id}>!",
        )


_intros_group.register(IntrosMissing)
loader.command(_intros_group)
