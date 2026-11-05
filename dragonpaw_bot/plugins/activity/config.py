"""Slash commands: /config activity role add/remove, channel add/remove, viewer, status; /config lurker"""

from __future__ import annotations

import datetime

import hikari
import lightbulb
import structlog

from dragonpaw_bot.colors import SOLARIZED_BLUE
from dragonpaw_bot.context import (
    GuildContext,
    actor_name,
    check_guild_perms,
    check_role_manageable,
    guild_owner_only,
)
from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.models import (
    ActivityGuildConfig,
    ActivityGuildMeta,
    ChannelConfig,
    RoleConfig,
)
from dragonpaw_bot.plugins.activity.purge import cancel_purge, start_purge
from dragonpaw_bot.utils import guild_members

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

        label, contrib_mult, decay_mult, ignored = _ROLE_PRESETS[self.preset]

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
        actor = actor_name(ctx)
        await gc.log(
            f"📊 **{actor}** set **{self.role.name}** to activity preset **{label}**"
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
        actor = actor_name(ctx)
        await gc.log(
            f"📊 **{actor}** removed activity config for **{self.role.name}** 🐾"
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
        actor = actor_name(ctx)
        await gc.log(
            f"📊 **{actor}** set <#{self.channel.id}> to **{mult}×** activity points 🐉"
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
        actor = actor_name(ctx)
        await gc.log(
            f"📊 **{actor}** removed the activity multiplier for <#{self.channel.id}> 🐾"
        )


class LurkerConfig(
    lightbulb.SlashCommand,
    name="lurker",
    description="Lurker role and automatic lurker purges (no options shows settings)",
    hooks=[guild_owner_only],
):
    role = lightbulb.role("role", "The lurker role", default=None)
    enabled = lightbulb.boolean(
        "enabled", "Turn automatic lurker purges on or off", default=None
    )
    frequency = lightbulb.integer(
        "frequency", "Months between purges", min_value=1, max_value=12, default=None
    )
    grace = lightbulb.integer(
        "grace",
        "Days staff have to spare members",
        min_value=1,
        max_value=14,
        default=None,
    )
    staff_role = lightbulb.role(
        "staff_role", "Role allowed to spare members and press Boot", default=None
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        gc = GuildContext.from_ctx(ctx)
        meta = activity_state.load_config(int(ctx.guild_id))
        meta.guild_name = gc.name
        cfg = meta.config

        options = (self.role, self.enabled, self.frequency, self.grace, self.staff_role)
        if all(option is None for option in options):
            await ctx.respond(_lurker_summary(meta), flags=hikari.MessageFlag.EPHEMERAL)
            return

        was_enabled = cfg.purge_enabled
        changes: list[str] = []
        notes: list[str] = []

        if self.role is not None:
            cfg.lurker_role_id = int(self.role.id)
            cfg.lurker_role_name = self.role.name
            changes.append(f"lurker role **{self.role.name}**")
            warning = await check_role_manageable(gc.bot, ctx.guild_id, self.role)
            if warning:
                notes.append(f"⚠️ {warning}")
        if self.frequency is not None:
            cfg.purge_interval_months = self.frequency
            changes.append(f"purge every **{self.frequency}** month(s)")
        if self.grace is not None:
            cfg.purge_grace_days = self.grace
            changes.append(f"**{self.grace}** day(s) to spare")
        if self.staff_role is not None:
            cfg.purge_staff_role_id = int(self.staff_role.id)
            changes.append(f"purge staff role **{self.staff_role.name}**")

        if self.enabled is False and was_enabled:
            cfg.purge_enabled = False
            changes.append("purges **off**")
        elif self.enabled and not was_enabled:
            problems = await _try_enable(gc, ctx.guild_id, cfg)
            notes.extend(problems)
            if cfg.purge_enabled:
                changes.append("purges **on**")

        activity_state.save_config(meta)

        saved = ", ".join(changes) if changes else "nothing changed"
        await ctx.respond(
            "\n".join([f"🐉 Saved: {saved} 🐾", *notes]),
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        gc.logger.info("Lurker settings changed", changes=changes)
        if changes:
            actor = actor_name(ctx)
            await gc.log(
                f"⚙️ **{actor}** updated the lurker settings: {', '.join(changes)} 🐉"
            )

        if cfg.purge_enabled and not was_enabled:
            members = await guild_members(gc.bot, ctx.guild_id)
            await start_purge(gc, meta, members, datetime.datetime.now(tz=datetime.UTC))
            activity_state.save_config(meta)
        elif was_enabled and not cfg.purge_enabled and meta.purge is not None:
            await cancel_purge(gc, meta)
            activity_state.save_config(meta)


async def _try_enable(
    gc: GuildContext, guild_id: hikari.Snowflake, cfg: ActivityGuildConfig
) -> list[str]:
    """Turn purges on if possible; return notes for whatever stopped or worried us."""
    if not cfg.lurker_role_id:
        return [
            "🐉 I can't start purging yet — I don't know who the lurkers are!"
            " Set the lurker role with `role:` first. 🐾"
        ]
    if not gc.log_channel_id:
        return [
            "🐉 I can't start purging yet — I post the list in the staff log"
            " channel and I don't have one! Set it with `/config channels log`"
            " first. 🐾"
        ]
    cfg.purge_enabled = True
    missing = await check_guild_perms(
        gc.bot, guild_id, {hikari.Permissions.KICK_MEMBERS: "Kick Members"}
    )
    if missing:
        return [
            "⚠️ I'm missing **Kick Members**, so I won't be able to boot anyone"
            " until that's fixed."
        ]
    return []


def _lurker_summary(meta: ActivityGuildMeta) -> str:
    cfg = meta.config
    role = f"<@&{cfg.lurker_role_id}>" if cfg.lurker_role_id else "_not set_"
    staff = (
        f"<@&{cfg.purge_staff_role_id}>" if cfg.purge_staff_role_id else "_owner only_"
    )
    lines = [
        "🐉 **Lurker settings**",
        f"Lurker role: {role}",
        f"Purges: **{'on' if cfg.purge_enabled else 'off'}**,"
        f" every {cfg.purge_interval_months} month(s),"
        f" {cfg.purge_grace_days} day(s) to spare",
        f"Purge staff role: {staff}",
    ]
    purge = meta.purge
    if purge is not None:
        if purge.boot_posted_at is not None:
            lines.append(
                "A purge is in progress: the final list is up, waiting on Boot."
            )
        else:
            lines.append(
                f"A purge is in progress: day {purge.day} of {cfg.purge_grace_days}."
            )
    return "\n".join(lines)


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
            actor = actor_name(ctx)
            await gc.log(
                f"📊 **{actor}** cleared the activity viewer role — admin-only access now 🐾"
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
        actor = actor_name(ctx)
        await gc.log(
            f"📊 **{actor}** set the activity viewer role to **{self.role.name}** 🐉"
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
            lurker_text = f"<@&{cfg.lurker_role_id}> (see /config lurker)"
        else:
            lurker_text = "_Not set_ (see /config lurker)"
        if cfg.viewer_role_id:
            viewer_text = f"<@&{cfg.viewer_role_id}>"
        else:
            viewer_text = "_Manage Server only_"

        embed = hikari.Embed(
            title="📊 Activity Tracker Configuration",
            color=SOLARIZED_BLUE,
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
