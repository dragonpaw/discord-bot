from __future__ import annotations

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import (
    GuildContext,
    check_channel_perms,
    check_role_manageable,
    guild_owner_only,
)
from dragonpaw_bot.plugins.validation import state as validation_state

logger = structlog.get_logger(__name__)


def register(sub: lightbulb.SubGroup) -> None:
    """Register /config validation subcommands."""
    sub.register(ValidationSetup)
    sub.register(ValidationStatus)


class ValidationSetup(
    lightbulb.SlashCommand,
    name="setup",
    description="Configure the member validation system.",
    hooks=[guild_owner_only],
):
    lobby_channel = lightbulb.channel(
        "lobby_channel",
        "Channel where new members get their welcome message",
        default=None,
        channel_types=[hikari.ChannelType.GUILD_TEXT],
    )
    validate_category = lightbulb.channel(
        "validate_category",
        "Category to create private validate channels under",
        default=None,
        channel_types=[hikari.ChannelType.GUILD_CATEGORY],
    )
    member_role = lightbulb.role(
        "member_role",
        "Role assigned to members when they are approved",
        default=None,
    )
    staff_role = lightbulb.role(
        "staff_role",
        "Staff role added to validate channels and pinged on photo submission",
        default=None,
    )
    about_channel = lightbulb.channel(
        "about_channel",
        "Channel linked in the welcome message for server info",
        default=None,
        channel_types=[hikari.ChannelType.GUILD_TEXT],
    )
    roles_channel = lightbulb.channel(
        "roles_channel",
        "Channel linked in the welcome message for role selection",
        default=None,
        channel_types=[hikari.ChannelType.GUILD_TEXT],
    )
    intros_channel = lightbulb.channel(
        "intros_channel",
        "Channel linked in the welcome message for introductions",
        default=None,
        channel_types=[hikari.ChannelType.GUILD_TEXT],
    )
    events_channel = lightbulb.channel(
        "events_channel",
        "Channel linked in the welcome message for classes and events",
        default=None,
        channel_types=[hikari.ChannelType.GUILD_TEXT],
    )
    chat_channel = lightbulb.channel(
        "chat_channel",
        "Channel linked in the welcome message for general chat",
        default=None,
        channel_types=[hikari.ChannelType.GUILD_TEXT],
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:  # noqa: PLR0912, PLR0915
        if not ctx.guild_id:
            return
        gc = GuildContext.from_ctx(ctx)
        st = validation_state.load(int(ctx.guild_id))
        st.guild_name = gc.name

        if self.lobby_channel is not None:
            st.lobby_channel_id = int(self.lobby_channel.id)
        if self.validate_category is not None:
            st.validate_category_id = int(self.validate_category.id)
        if self.member_role is not None:
            st.member_role_id = int(self.member_role.id)
        if self.staff_role is not None:
            st.staff_role_id = int(self.staff_role.id)
        if self.about_channel is not None:
            st.about_channel_id = int(self.about_channel.id)
        if self.roles_channel is not None:
            st.roles_channel_id = int(self.roles_channel.id)
        if self.intros_channel is not None:
            st.intros_channel_id = int(self.intros_channel.id)
        if self.events_channel is not None:
            st.events_channel_id = int(self.events_channel.id)
        if self.chat_channel is not None:
            st.chat_channel_id = int(self.chat_channel.id)

        validation_state.save(st)
        gc.logger.info(
            "Configured validation",
            lobby_channel=self.lobby_channel.name if self.lobby_channel else None,
            validate_category=self.validate_category.name
            if self.validate_category
            else None,
            member_role=self.member_role.name if self.member_role else None,
            staff_role=self.staff_role.name if self.staff_role else None,
        )

        parts = []
        if self.lobby_channel:
            parts.append(f"lobby: <#{self.lobby_channel.id}>")
        if self.validate_category:
            parts.append(f"validate category: {self.validate_category.name}")
        if self.member_role:
            parts.append(f"member role: <@&{self.member_role.id}>")
        if self.staff_role:
            parts.append(f"staff role: <@&{self.staff_role.id}>")
        if self.about_channel:
            parts.append(f"about: <#{self.about_channel.id}>")
        if self.roles_channel:
            parts.append(f"roles: <#{self.roles_channel.id}>")
        if self.intros_channel:
            parts.append(f"intros: <#{self.intros_channel.id}>")
        if self.events_channel:
            parts.append(f"events: <#{self.events_channel.id}>")
        if self.chat_channel:
            parts.append(f"chat: <#{self.chat_channel.id}>")

        summary = ", ".join(parts) if parts else "no changes"
        warnings: list[str] = []

        if self.validate_category is not None:
            missing = await check_channel_perms(
                gc.bot,
                ctx.guild_id,
                self.validate_category.id,
                {hikari.Permissions.MANAGE_CHANNELS: "Manage Channels"},
            )
            if missing:
                warnings.append(
                    "⚠️ I'm missing **Manage Channels** in the validate category — "
                    "I won't be able to create or delete validate channels!"
                )

        if self.member_role is not None:
            reason = await check_role_manageable(gc.bot, ctx.guild_id, self.member_role)
            if reason:
                warnings.append(f"⚠️ Member role issue: {reason}")

        members_approved = (
            hikari.ApplicationFlags.GUILD_MEMBERS_INTENT
            | hikari.ApplicationFlags.VERIFIED_FOR_GUILD_MEMBERS_INTENT
        )
        flags = gc.bot.application_flags
        if not (gc.bot.intents & hikari.Intents.GUILD_MEMBERS) or (
            flags is not None and not (flags & members_approved)
        ):
            warnings.append(
                "⚠️ **Server Members Intent** is not enabled — I won't receive member join "
                "events and the onboarding flow will silently do nothing! Enable it under: "
                "Discord Developer Portal → Bot → Privileged Gateway Intents → "
                "Server Members Intent"
            )

        warning_text = ("\n" + "\n".join(warnings)) if warnings else ""
        await ctx.respond(
            f"*happy tail wag* 🐉 Validation settings updated — {summary}!{warning_text}",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        await gc.log(f"⚙️ {ctx.user.mention} updated validation settings — {summary} 🐾")


class ValidationStatus(
    lightbulb.SlashCommand,
    name="status",
    description="Show current validation configuration and member counts.",
    hooks=[guild_owner_only],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            return
        st = validation_state.load(int(ctx.guild_id))

        awaiting_rules = sum(1 for m in st.members if m.stage.value == "awaiting_rules")
        awaiting_photos = sum(
            1 for m in st.members if m.stage.value == "awaiting_photos"
        )
        awaiting_staff = sum(1 for m in st.members if m.stage.value == "awaiting_staff")

        bot: hikari.GatewayBot = ctx.client.app  # type: ignore[assignment]
        category_ch = (
            bot.cache.get_guild_channel(hikari.Snowflake(st.validate_category_id))
            if st.validate_category_id
            else None
        )
        category_display = (
            category_ch.name
            if category_ch
            else str(st.validate_category_id)
            if st.validate_category_id
            else "not set"
        )

        lines = ["*peers around curiously* 🐉 Here's my validation setup:"]
        lines.append(
            f"• Lobby channel: {f'<#{st.lobby_channel_id}>' if st.lobby_channel_id else 'not set'}"
        )
        lines.append(f"• Validate category: {category_display}")
        lines.append(
            f"• Member role: {f'<@&{st.member_role_id}>' if st.member_role_id else 'not set'}"
        )
        lines.append(
            f"• Staff role: {f'<@&{st.staff_role_id}>' if st.staff_role_id else 'not set'}"
        )
        lines.append("• Validation timeout: 7 days from join, reminders every 18 hours")
        lines.append(
            f"• About channel: {f'<#{st.about_channel_id}>' if st.about_channel_id else 'not set'}"
        )
        lines.append(
            f"• Roles channel: {f'<#{st.roles_channel_id}>' if st.roles_channel_id else 'not set'}"
        )
        lines.append(
            f"• Intros channel: {f'<#{st.intros_channel_id}>' if st.intros_channel_id else 'not set'}"
        )
        lines.append(
            f"• Events channel: {f'<#{st.events_channel_id}>' if st.events_channel_id else 'not set'}"
        )
        lines.append(
            f"• Chat channel: {f'<#{st.chat_channel_id}>' if st.chat_channel_id else 'not set'}"
        )
        lines.append(f"• Awaiting rules: {awaiting_rules}")
        lines.append(f"• Awaiting photos: {awaiting_photos}")
        lines.append(f"• Awaiting staff review: {awaiting_staff}")

        await ctx.respond("\n".join(lines), flags=hikari.MessageFlag.EPHEMERAL)
