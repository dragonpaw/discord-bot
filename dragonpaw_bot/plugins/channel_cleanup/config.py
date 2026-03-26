"""Slash commands: /config cleanup add|remove|status"""

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
from dragonpaw_bot.duration import format_duration, parse_duration_minutes
from dragonpaw_bot.plugins.channel_cleanup import state as cleanup_state
from dragonpaw_bot.plugins.channel_cleanup.models import CleanupChannelEntry

logger = structlog.get_logger(__name__)


def register(cleanup_sub: lightbulb.SubGroup) -> None:
    cleanup_sub.register(CleanupAdd)
    cleanup_sub.register(CleanupRemove)
    cleanup_sub.register(CleanupStatus)


class CleanupAdd(
    lightbulb.SlashCommand,
    name="add",
    description="Add a channel for automatic message expiry.",
    hooks=[guild_owner_only],
):
    channel = lightbulb.channel("channel", "Channel to auto-clean")
    expires = lightbulb.string(
        "expires",
        "Delete messages older than this. Format: 30m, 6h, 2d, 1w, 1d12h",
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        gc = GuildContext.from_ctx(ctx)

        try:
            expiry_minutes = parse_duration_minutes(self.expires)
        except ValueError as exc:
            await ctx.respond(str(exc), flags=hikari.MessageFlag.EPHEMERAL)
            return

        st = cleanup_state.load(int(ctx.guild_id))
        st.guild_name = gc.name

        # Replace existing entry for this channel if present
        st.channels = [c for c in st.channels if c.channel_id != self.channel.id]
        st.channels.append(
            CleanupChannelEntry(
                channel_id=int(self.channel.id),
                channel_name=self.channel.name or str(self.channel.id),
                expiry_minutes=expiry_minutes,
            )
        )
        cleanup_state.save(st)

        gc.logger.info(
            "Added cleanup channel",
            channel=self.channel.name,
            expiry_minutes=expiry_minutes,
        )

        missing = await check_channel_perms(
            gc.bot, ctx.guild_id, self.channel.id, CHANNEL_CLEANUP_PERMS
        )
        warning = ""
        if missing:
            warning = (
                f"\n⚠️ I'm missing permissions in that channel: "
                f"**{', '.join(missing)}**. Cleanup won't work until that's fixed."
            )

        await ctx.respond(
            f"<#{self.channel.id}> will be cleaned of messages older than "
            f"{format_duration(expiry_minutes)}.{warning}",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        await gc.log(
            f"⚙️ {ctx.user.mention} put me on tidy-up duty in <#{self.channel.id}> — "
            f"I'll nom messages older than {format_duration(expiry_minutes)}! 🧹",
        )


class CleanupRemove(
    lightbulb.SlashCommand,
    name="remove",
    description="Stop auto-cleaning a channel.",
    hooks=[guild_owner_only],
):
    channel = lightbulb.channel("channel", "Channel to remove from auto-cleanup")

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        gc = GuildContext.from_ctx(ctx)

        st = cleanup_state.load(int(ctx.guild_id))
        before = len(st.channels)
        st.channels = [c for c in st.channels if c.channel_id != self.channel.id]

        if len(st.channels) == before:
            await ctx.respond(
                f"<#{self.channel.id}> is not a configured auto-cleanup channel.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        st.guild_name = gc.name
        cleanup_state.save(st)
        gc.logger.info("Removed cleanup channel", channel=self.channel.name)

        await ctx.respond(
            f"<#{self.channel.id}> removed from auto-cleanup.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        await gc.log(
            f"⚙️ {ctx.user.mention} took <#{self.channel.id}> off my cleanup list — I'll leave those messages alone now~ 🐉",
        )


class CleanupStatus(
    lightbulb.SlashCommand,
    name="status",
    description="Show all configured auto-cleanup channels.",
    hooks=[guild_owner_only],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return

        st = cleanup_state.load(int(ctx.guild_id))

        if not st.channels:
            await ctx.respond(
                "No auto-cleanup channels configured.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        lines = ["**Auto-cleanup channels:**"]
        lines.extend(
            f"• <#{entry.channel_id}> → expires: {format_duration(entry.expiry_minutes)}"
            for entry in st.channels
        )

        await ctx.respond("\n".join(lines), flags=hikari.MessageFlag.EPHEMERAL)
