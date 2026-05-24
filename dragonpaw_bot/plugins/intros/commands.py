"""Slash commands: /intros missing"""

from __future__ import annotations

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.intros import state as intros_state
from dragonpaw_bot.plugins.intros.cron import scan_intros

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

        result = await scan_intros(gc, st)

        gc.logger.info(
            "Intros missing check",
            channel=st.channel_name,
            missing_count=len(result.missing),
            role_added=len(result.role_added),
            role_removed=len(result.role_removed),
        )

        role_note = (
            f" with role **{st.required_role_name}**" if st.required_role_id else ""
        )
        role_action_lines: list[str] = []
        if st.missing_role_id and (result.role_added or result.role_removed):
            if result.role_added:
                role_action_lines.append(
                    f"➕ Added **{st.missing_role_name}** to {len(result.role_added)} member(s)."
                )
            if result.role_removed:
                role_action_lines.append(
                    f"➖ Removed **{st.missing_role_name}** from {len(result.role_removed)} member(s)."
                )
        if result.role_failed:
            role_action_lines.append(
                f"⚠️ I couldn't manage **{st.missing_role_name}** — check my role hierarchy!"
            )

        if not result.missing:
            head = f"Everyone{role_note} has posted an intro! 🐉"
            content = "\n".join([head, *role_action_lines]) if role_action_lines else head
            await ctx.edit_response(response_id, content=content)
        else:
            mentions = " ".join(m.mention for m in result.missing)
            header = (
                f"**{len(result.missing)} member(s){role_note} haven't posted an intro yet:**"
            )
            content = "\n".join([header, mentions, *role_action_lines])
            await ctx.edit_response(response_id, content=content)

        log_bits = [
            f"👀 {ctx.user.mention} asked who's been shy — "
            f"**{len(result.missing)}** member(s) still haven't introduced themselves in <#{st.channel_id}>."
        ]
        if st.missing_role_id and (result.role_added or result.role_removed):
            log_bits.append(
                f"Role **{st.missing_role_name}**: added to {len(result.role_added)}, "
                f"removed from {len(result.role_removed)}."
            )
        log_bits.append("🐉")
        await gc.log(" ".join(log_bits))


_intros_group.register(IntrosMissing)
loader.command(_intros_group)
