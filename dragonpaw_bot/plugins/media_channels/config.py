# -*- coding: utf-8 -*-
"""Slash commands: /config media add|remove|status"""
from __future__ import annotations

from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot import utils
from dragonpaw_bot.duration import format_duration, parse_duration_minutes
from dragonpaw_bot.plugins.media_channels import state as media_state
from dragonpaw_bot.plugins.media_channels.models import MediaChannelEntry

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)


def _get_bot(ctx: lightbulb.Context) -> DragonpawBot:
    bot: DragonpawBot = ctx.client.app  # type: ignore[assignment]
    return bot


def register(media_sub: lightbulb.SubGroup) -> None:
    media_sub.register(MediaAdd)
    media_sub.register(MediaRemove)
    media_sub.register(MediaStatus)


class MediaAdd(
    lightbulb.SlashCommand,
    name="add",
    description="Add a media-only channel (text-only posts will be deleted).",
    hooks=[lightbulb.prefab.has_permissions(hikari.Permissions.MANAGE_GUILD)],
):
    channel = lightbulb.channel("channel", "Channel to enforce media-only policy")
    redirect = lightbulb.channel(
        "redirect", "Channel to direct users to for text posts (optional)", default=None
    )
    expires = lightbulb.string(
        "expires",
        "Auto-delete messages older than this (optional). Format: 30m, 6h, 2d, 1w, 1d12h",
        default=None,
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)
        log = logger.bind(guild=guild.name, user=ctx.user.username)

        expiry_minutes: int | None = None
        if self.expires is not None:
            try:
                expiry_minutes = parse_duration_minutes(self.expires)
            except ValueError as exc:
                await ctx.respond(str(exc), flags=hikari.MessageFlag.EPHEMERAL)
                return

        st = media_state.load(int(ctx.guild_id))
        st.guild_name = guild.name

        # Replace existing entry for this channel if present
        st.channels = [c for c in st.channels if c.channel_id != self.channel.id]
        st.channels.append(
            MediaChannelEntry(
                channel_id=int(self.channel.id),
                channel_name=self.channel.name or str(self.channel.id),
                redirect_channel_id=int(self.redirect.id) if self.redirect else None,
                redirect_channel_name=(self.redirect.name or str(self.redirect.id))
                if self.redirect
                else None,
                expiry_minutes=expiry_minutes,
            )
        )
        media_state.save(st)

        parts = [f"🐉 <#{self.channel.id}> added as a media-only channel."]
        if self.redirect:
            parts.append(f"Text-post notices will redirect to <#{self.redirect.id}>.")
        if expiry_minutes is not None:
            parts.append(
                f"Messages older than {format_duration(expiry_minutes)} will be auto-deleted."
            )

        log.info(
            "Added media channel",
            channel=self.channel.name,
            redirect=self.redirect.name if self.redirect else None,
            expiry_minutes=expiry_minutes,
        )

        await ctx.respond("\n".join(parts), flags=hikari.MessageFlag.EPHEMERAL)
        await utils.log_to_guild(
            bot,
            ctx.guild_id,
            f"⚙️ {ctx.user.username} added <#{self.channel.id}> as a media-only channel.",
        )


class MediaRemove(
    lightbulb.SlashCommand,
    name="remove",
    description="Stop enforcing media-only policy in a channel.",
    hooks=[lightbulb.prefab.has_permissions(hikari.Permissions.MANAGE_GUILD)],
):
    channel = lightbulb.channel("channel", "Channel to remove from media-only enforcement")

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)
        log = logger.bind(guild=guild.name, user=ctx.user.username)

        st = media_state.load(int(ctx.guild_id))
        before = len(st.channels)
        st.channels = [c for c in st.channels if c.channel_id != self.channel.id]

        if len(st.channels) == before:
            await ctx.respond(
                f"<#{self.channel.id}> is not a configured media-only channel.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        st.guild_name = guild.name
        media_state.save(st)
        log.info("Removed media channel", channel=self.channel.name)

        await ctx.respond(
            f"<#{self.channel.id}> removed from media-only enforcement.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        await utils.log_to_guild(
            bot,
            ctx.guild_id,
            f"⚙️ {ctx.user.username} removed <#{self.channel.id}> from media-only enforcement.",
        )


class MediaStatus(
    lightbulb.SlashCommand,
    name="status",
    description="Show all configured media-only channels.",
    hooks=[lightbulb.prefab.has_permissions(hikari.Permissions.MANAGE_GUILD)],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return

        st = media_state.load(int(ctx.guild_id))

        if not st.channels:
            await ctx.respond(
                "No media-only channels configured.", flags=hikari.MessageFlag.EPHEMERAL
            )
            return

        lines = ["**Media-only channels:**"]
        for entry in st.channels:
            parts = [f"• <#{entry.channel_id}>"]
            if entry.redirect_channel_id:
                parts.append(f"→ redirect: <#{entry.redirect_channel_id}>")
            if entry.expiry_minutes is not None:
                parts.append(f"→ expires: {format_duration(entry.expiry_minutes)}")
            lines.append(" ".join(parts))

        await ctx.respond("\n".join(lines), flags=hikari.MessageFlag.EPHEMERAL)
