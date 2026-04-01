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
    announce_channel = lightbulb.channel(
        "announce_channel",
        "Channel where approved members are announced",
        default=None,
        channel_types=[hikari.ChannelType.GUILD_TEXT],
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
    max_reminders = lightbulb.integer(
        "max_reminders",
        "How many 24h lobby reminders before auto-kick (default: 3)",
        default=None,
        min_value=1,
        max_value=10,
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:  # noqa: PLR0912
        if not ctx.guild_id:
            return
        gc = GuildContext.from_ctx(ctx)
        st = validation_state.load(int(ctx.guild_id))
        st.guild_name = gc.name

        if self.lobby_channel is not None:
            st.lobby_channel_id = int(self.lobby_channel.id)
        if self.validate_category is not None:
            st.validate_category_id = int(self.validate_category.id)
        if self.announce_channel is not None:
            st.general_channel_id = int(self.announce_channel.id)
        if self.member_role is not None:
            st.member_role_id = int(self.member_role.id)
        if self.staff_role is not None:
            st.staff_role_id = int(self.staff_role.id)
        if self.max_reminders is not None:
            st.max_reminders = self.max_reminders

        validation_state.save(st)
        gc.logger.info(
            "Configured validation",
            lobby_channel=self.lobby_channel.name if self.lobby_channel else None,
            validate_category=self.validate_category.name
            if self.validate_category
            else None,
            announce_channel=self.announce_channel.name
            if self.announce_channel
            else None,
            member_role=self.member_role.name if self.member_role else None,
            staff_role=self.staff_role.name if self.staff_role else None,
            max_reminders=self.max_reminders,
        )

        parts = []
        if self.lobby_channel:
            parts.append(f"lobby: <#{self.lobby_channel.id}>")
        if self.validate_category:
            parts.append(f"validate category: {self.validate_category.name}")
        if self.announce_channel:
            parts.append(f"announce: <#{self.announce_channel.id}>")
        if self.member_role:
            parts.append(f"member role: <@&{self.member_role.id}>")
        if self.staff_role:
            parts.append(f"staff role: <@&{self.staff_role.id}>")
        if self.max_reminders is not None:
            parts.append(f"max reminders: {self.max_reminders}")

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

        bot = ctx.client.app
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
            f"• Announce channel: {f'<#{st.general_channel_id}>' if st.general_channel_id else 'not set'}"
        )
        lines.append(
            f"• Member role: {f'<@&{st.member_role_id}>' if st.member_role_id else 'not set'}"
        )
        lines.append(
            f"• Staff role: {f'<@&{st.staff_role_id}>' if st.staff_role_id else 'not set'}"
        )
        lines.append(f"• Max reminders before kick: {st.max_reminders}")
        lines.append(f"• Awaiting rules: {awaiting_rules}")
        lines.append(f"• Awaiting photos: {awaiting_photos}")
        lines.append(f"• Awaiting staff review: {awaiting_staff}")

        await ctx.respond("\n".join(lines), flags=hikari.MessageFlag.EPHEMERAL)
