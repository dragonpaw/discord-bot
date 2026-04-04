"""Slash commands: /config activity role add/remove, channel add/remove, lurker, status"""

from __future__ import annotations

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext, check_role_manageable, guild_owner_only
from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.models import ChannelConfig, RoleConfig

logger = structlog.get_logger(__name__)

# Preset choices: (label, contribution_multiplier, decay_multiplier, ignored)
_ROLE_PRESETS: dict[str, tuple[str, float, float, bool]] = {
    "standard": ("Standard", 1.0, 1.0, False),
    "active": ("Active", 1.1, 1.3, False),
    "veteran": ("Veteran", 1.2, 1.7, False),
    "ignore": ("Ignore (staff/exempt)", 1.0, 1.0, True),
}

_PRESET_CHOICES = [
    lightbulb.Choice(name=label, value=key)
    for key, (label, *_) in _ROLE_PRESETS.items()
]


def register(activity_sub: lightbulb.SubGroup) -> None:
    activity_sub.register(ActivityRoleAdd)
    activity_sub.register(ActivityRoleRemove)
    activity_sub.register(ActivityChannelAdd)
    activity_sub.register(ActivityChannelRemove)
    activity_sub.register(ActivityLurker)
    activity_sub.register(ActivityViewer)
    activity_sub.register(ActivityStatus)


class ActivityRoleAdd(
    lightbulb.SlashCommand,
    name="role-add",
    description="Add or update a role's activity preset",
    hooks=[guild_owner_only],
):
    role = lightbulb.role("role", "The role to configure")
    preset = lightbulb.string(
        "preset",
        "Activity level for this role",
        choices=_PRESET_CHOICES,
        default="standard",
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)

        preset_key = self.preset or "standard"
        label, contrib_mult, decay_mult, ignored = _ROLE_PRESETS[preset_key]

        meta = activity_state.load_config(int(ctx.guild_id))
        meta.guild_name = gc.name

        # Upsert role config by role_id
        existing = next(
            (rc for rc in meta.config.role_configs if rc.role_id == int(self.role.id)),
            None,
        )
        if existing is not None:
            meta.config.role_configs.remove(existing)

        meta.config.role_configs.append(
            RoleConfig(
                role_id=int(self.role.id),
                role_name=self.role.name,
                contribution_multiplier=contrib_mult,
                decay_multiplier=decay_mult,
                ignored=ignored,
            )
        )
        activity_state.save_config(meta)

        await ctx.respond(
            f"🐉 Role **{self.role.name}** set to preset **{label}**!"
            + (
                " (activity will not be tracked for this role) 🐾" if ignored else " 🐾"
            ),
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        gc.logger.info(
            "Activity role configured",
            role=self.role.name,
            preset=label,
        )
        await gc.log(
            f"📊 {ctx.user.mention} set **{self.role.name}** to activity preset **{label}**"
            + (
                " — I'll ignore them! 🙈"
                if ignored
                else f" (×{contrib_mult} contribution, ×{decay_mult} decay) 🐉"
            )
        )


class ActivityRoleRemove(
    lightbulb.SlashCommand,
    name="role-remove",
    description="Remove a role's activity configuration",
    hooks=[guild_owner_only],
):
    role = lightbulb.role("role", "The role to remove")

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)

        meta = activity_state.load_config(int(ctx.guild_id))
        before = len(meta.config.role_configs)
        meta.config.role_configs = [
            rc for rc in meta.config.role_configs if rc.role_id != int(self.role.id)
        ]

        if len(meta.config.role_configs) == before:
            await ctx.respond(
                f"🐉 **{self.role.name}** isn't configured — nothing to remove! 🐾",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        meta.guild_name = gc.name
        activity_state.save_config(meta)

        await ctx.respond(
            f"🐉 Removed activity config for **{self.role.name}** 🐾",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        gc.logger.info("Activity role removed", role=self.role.name)
        await gc.log(
            f"📊 {ctx.user.mention} removed activity config for **{self.role.name}** 🐾"
        )


class ActivityChannelAdd(
    lightbulb.SlashCommand,
    name="channel-add",
    description="Add or update a channel's point multiplier",
    hooks=[guild_owner_only],
):
    channel = lightbulb.channel("channel", "The channel to configure")
    multiplier = lightbulb.number(
        "multiplier",
        "Point multiplier for posts in this channel (e.g. 2.0 for double points)",
        default=2.0,
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)

        mult = max(0.0, self.multiplier)  # guard against negative
        meta = activity_state.load_config(int(ctx.guild_id))
        meta.guild_name = gc.name

        existing = next(
            (
                cc
                for cc in meta.config.channel_configs
                if cc.channel_id == int(self.channel.id)
            ),
            None,
        )
        if existing is not None:
            meta.config.channel_configs.remove(existing)

        meta.config.channel_configs.append(
            ChannelConfig(
                channel_id=int(self.channel.id),
                channel_name=self.channel.name or str(self.channel.id),
                point_multiplier=mult,
            )
        )
        activity_state.save_config(meta)

        await ctx.respond(
            f"🐉 <#{self.channel.id}> set to **{mult}×** points 🐾",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        gc.logger.info(
            "Activity channel configured",
            channel=self.channel.name,
            multiplier=mult,
        )
        await gc.log(
            f"📊 {ctx.user.mention} set <#{self.channel.id}> to **{mult}×** activity points 🐉"
        )


class ActivityChannelRemove(
    lightbulb.SlashCommand,
    name="channel-remove",
    description="Remove a channel's point multiplier",
    hooks=[guild_owner_only],
):
    channel = lightbulb.channel("channel", "The channel to remove")

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)

        meta = activity_state.load_config(int(ctx.guild_id))
        before = len(meta.config.channel_configs)
        meta.config.channel_configs = [
            cc
            for cc in meta.config.channel_configs
            if cc.channel_id != int(self.channel.id)
        ]

        if len(meta.config.channel_configs) == before:
            await ctx.respond(
                f"🐉 <#{self.channel.id}> isn't configured — nothing to remove! 🐾",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        meta.guild_name = gc.name
        activity_state.save_config(meta)

        await ctx.respond(
            f"🐉 Removed multiplier config for <#{self.channel.id}> 🐾",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        gc.logger.info("Activity channel removed", channel=self.channel.name)
        await gc.log(
            f"📊 {ctx.user.mention} removed the activity multiplier for <#{self.channel.id}> 🐾"
        )


class ActivityLurker(
    lightbulb.SlashCommand,
    name="lurker",
    description="Set (or clear) the lurker role assigned to inactive members",
    hooks=[guild_owner_only],
):
    role = lightbulb.role("role", "The lurker role (omit to clear)", default=None)

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)

        meta = activity_state.load_config(int(ctx.guild_id))
        meta.guild_name = gc.name

        if self.role is None:
            meta.config.lurker_role_id = None
            meta.config.lurker_role_name = ""
            activity_state.save_config(meta)
            await ctx.respond(
                "🐉 Lurker role cleared — I won't tag inactive members anymore 🐾",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            gc.logger.info("Activity lurker role cleared")
            await gc.log(
                f"📊 {ctx.user.mention} cleared the lurker role — no more lurker tags! 🐾"
            )
            return

        # Check bot can manage the role
        warning = await check_role_manageable(gc.bot, ctx.guild_id, self.role)

        meta.config.lurker_role_id = int(self.role.id)
        meta.config.lurker_role_name = self.role.name
        activity_state.save_config(meta)

        warn_suffix = f"\n⚠️ {warning}" if warning else ""
        await ctx.respond(
            f"🐉 Lurker role set to **{self.role.name}** — I'll assign it daily to inactive members 🐾{warn_suffix}",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        gc.logger.info("Activity lurker role set", role=self.role.name)
        await gc.log(
            f"📊 {ctx.user.mention} set the lurker role to **{self.role.name}** 🐉"
            + (f" ⚠️ {warning}" if warning else " *happy tail wag* 🐾")
        )


class ActivityViewer(
    lightbulb.SlashCommand,
    name="viewer",
    description="Set (or clear) the role required to use /activity commands",
    hooks=[guild_owner_only],
):
    role = lightbulb.role("role", "The viewer role (omit to clear)", default=None)

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)

        meta = activity_state.load_config(int(ctx.guild_id))
        meta.guild_name = gc.name

        if self.role is None:
            meta.config.viewer_role_id = None
            meta.config.viewer_role_name = ""
            activity_state.save_config(meta)
            await ctx.respond(
                "🐉 Viewer role cleared — only Manage Server can use /activity commands now 🐾",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            gc.logger.info("Activity viewer role cleared")
            await gc.log(
                f"📊 {ctx.user.mention} cleared the activity viewer role — admin-only access now 🐾"
            )
            return

        meta.config.viewer_role_id = int(self.role.id)
        meta.config.viewer_role_name = self.role.name
        activity_state.save_config(meta)

        await ctx.respond(
            f"🐉 Viewer role set to **{self.role.name}** — members with this role can use /activity commands 🐾",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        gc.logger.info("Activity viewer role set", role=self.role.name)
        await gc.log(
            f"📊 {ctx.user.mention} set the activity viewer role to **{self.role.name}** 🐉"
        )


class ActivityStatus(
    lightbulb.SlashCommand,
    name="status",
    description="Show activity tracker configuration",
    hooks=[guild_owner_only],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        meta = activity_state.load_config(int(ctx.guild_id))
        cfg = meta.config

        # Roles section
        if cfg.role_configs:
            role_lines = []
            for rc in cfg.role_configs:
                if rc.ignored:
                    role_lines.append(f"• **{rc.role_name}** — ignored")
                else:
                    role_lines.append(
                        f"• **{rc.role_name}** — ×{rc.contribution_multiplier} contribution, ×{rc.decay_multiplier} decay"
                    )
            roles_text = "\n".join(role_lines)
        else:
            roles_text = "_None configured_"

        # Channels section
        if cfg.channel_configs:
            channel_lines = [
                f"• <#{cc.channel_id}> — ×{cc.point_multiplier}"
                for cc in cfg.channel_configs
            ]
            channels_text = "\n".join(channel_lines)
        else:
            channels_text = "_None configured_"

        if cfg.lurker_role_id:
            lurker_text = f"<@&{cfg.lurker_role_id}>"
        else:
            lurker_text = "_Not set_"
        if cfg.viewer_role_id:
            viewer_text = f"<@&{cfg.viewer_role_id}>"
        else:
            viewer_text = "_Manage Server only_"

        embed = hikari.Embed(
            title="📊 Activity Tracker Configuration",
            color=0x268BD2,
        )
        embed.add_field(name="Roles", value=roles_text, inline=False)
        embed.add_field(name="Channel Multipliers", value=channels_text, inline=False)
        embed.add_field(name="Lurker Role", value=lurker_text, inline=False)
        embed.add_field(name="Viewer Role", value=viewer_text, inline=False)
        embed.add_field(
            name="Tracked Members",
            value=str(len(activity_state.list_user_ids(int(ctx.guild_id)))),
            inline=True,
        )

        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
