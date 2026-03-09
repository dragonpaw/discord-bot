# -*- coding: utf-8 -*-
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot
    from dragonpaw_bot.structs import GuildState

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------- #
#                                GuildContext                                   #
# ---------------------------------------------------------------------------- #


@dataclasses.dataclass
class GuildContext:
    """Bundles bot + guild info for convenient access throughout plugins."""

    bot: DragonpawBot
    guild_id: hikari.Snowflake
    name: str
    log_channel_id: hikari.Snowflake | None
    member: hikari.Member | None = None
    logger: structlog.stdlib.BoundLogger = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        self.logger = structlog.get_logger(__name__).bind(guild=self.name)

    # --- Factory methods ---

    @classmethod
    def from_ctx(cls, ctx: lightbulb.Context) -> GuildContext:
        """From a slash command context. Sets member from ctx.member."""
        assert ctx.guild_id
        bot: DragonpawBot = ctx.client.app  # type: ignore[assignment]
        guild = bot.cache.get_guild(ctx.guild_id)
        name = guild.name if guild else str(ctx.guild_id)
        state = bot.state(ctx.guild_id)
        log_channel_id = state.log_channel_id if state else None
        gc = cls(
            bot=bot,
            guild_id=ctx.guild_id,
            name=name,
            log_channel_id=log_channel_id,
            member=ctx.member,
        )
        if ctx.member:
            gc.logger = gc.logger.bind(user=ctx.member.display_name)
        return gc

    @classmethod
    def from_interaction(cls, interaction: hikari.ComponentInteraction) -> GuildContext:
        """From a component interaction. Sets member from interaction.member."""
        assert interaction.guild_id
        bot: DragonpawBot = interaction.app  # type: ignore[assignment]
        guild = bot.cache.get_guild(interaction.guild_id)
        name = guild.name if guild else str(interaction.guild_id)
        state = bot.state(interaction.guild_id)
        log_channel_id = state.log_channel_id if state else None
        return cls(
            bot=bot,
            guild_id=interaction.guild_id,
            name=name,
            log_channel_id=log_channel_id,
            member=interaction.member,
        )

    @classmethod
    def from_guild(cls, bot: DragonpawBot, guild: hikari.Guild) -> GuildContext:
        """From a cached guild (cron tasks). No member."""
        state = bot.state(guild.id)
        log_channel_id = state.log_channel_id if state else None
        return cls(
            bot=bot,
            guild_id=guild.id,
            name=guild.name,
            log_channel_id=log_channel_id,
        )

    # --- Convenience methods ---

    async def log(self, message: str) -> None:
        """Send to guild log channel. No-op if unconfigured."""
        if not self.log_channel_id:
            self.logger.debug("No log channel configured, skipping log message")
            return
        try:
            await self.bot.rest.create_message(
                channel=self.log_channel_id, content=message
            )
        except hikari.HTTPError as exc:
            self.logger.warning("Failed to send log message", error=str(exc))

    def state(self) -> GuildState | None:
        """Get the guild's core GuildState."""
        return self.bot.state(self.guild_id)

    async def fetch_guild(self) -> hikari.Guild | hikari.RESTGuild:
        """Cache-first guild fetch."""
        guild = self.bot.cache.get_guild(self.guild_id)
        if guild:
            return guild
        self.logger.debug("Guild not in cache, fetching via REST")
        return await self.bot.rest.fetch_guild(self.guild_id)

    def is_owner(self) -> bool:
        """Check if member is the guild owner. Requires member to be set."""
        assert self.member
        guild = self.bot.cache.get_guild(self.guild_id)
        if guild:
            return self.member.id == guild.owner_id
        return False

    def has_permission(self, role_name: str | None) -> bool:
        """Check if member has the named role or is guild owner."""
        assert self.member
        guild = self.bot.cache.get_guild(self.guild_id)
        if guild and self.member.id == guild.owner_id:
            return True
        if role_name:
            return member_has_role(self.member, role_name)
        return False

    def has_any_permission(self, role_names: list[str]) -> bool:
        """Check if member has any of the named roles or is guild owner."""
        assert self.member
        guild = self.bot.cache.get_guild(self.guild_id)
        if guild and self.member.id == guild.owner_id:
            return True
        for name in role_names:
            if member_has_role(self.member, name):
                return True
        return False

    async def check_permission(
        self, ctx: lightbulb.Context, role_name: str | list[str] | None, action: str
    ) -> bool:
        """Check permission, respond with denial if lacking. Returns True if allowed."""
        assert self.member
        guild = await self.fetch_guild()
        if isinstance(role_name, list):
            allowed = has_any_role_permission(guild, self.member, role_name)
            label = (
                "one of the **" + "**, **".join(role_name) + "** roles"
                if role_name
                else "server owner status"
            )
        else:
            allowed = has_permission(guild, self.member, role_name)
            label = f"**{role_name}** role" if role_name else "server owner status"
        if allowed:
            return True
        self.logger.warning(
            "Command denied, missing permission",
            action=action,
            required=label,
        )
        await ctx.respond(
            f"You need {label} to use this command.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return False

    async def require_owner(self, ctx: lightbulb.Context) -> bool:
        """Check guild owner, respond with denial if not. Returns True if allowed."""
        guild = await self.fetch_guild()
        if self.member and self.member.id == guild.owner_id:
            return True
        self.logger.warning("Admin command denied, not guild owner")
        await ctx.respond(
            "Only the server owner can use this command.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return False


# ---------------------------------------------------------------------------- #
#                               ChannelContext                                  #
# ---------------------------------------------------------------------------- #


@dataclasses.dataclass
class ChannelContext(GuildContext):
    """GuildContext extended with a specific channel for channel-level operations."""

    channel_id: hikari.Snowflake = dataclasses.field(default=hikari.Snowflake(0))
    channel_name: str = ""

    @classmethod
    def from_entry(cls, gc: GuildContext, entry: object) -> ChannelContext:
        """From a GuildContext + a channel entry (CleanupChannelEntry or MediaChannelEntry).

        Both entry types have channel_id and channel_name fields.
        """
        return cls(
            bot=gc.bot,
            guild_id=gc.guild_id,
            name=gc.name,
            log_channel_id=gc.log_channel_id,
            member=gc.member,
            channel_id=hikari.Snowflake(entry.channel_id),  # type: ignore[attr-defined]
            channel_name=entry.channel_name,  # type: ignore[attr-defined]
        )

    async def purge_old_messages(
        self, expiry_minutes: int, single_delete_limit: int = 1000
    ) -> int:
        """Delete messages older than expiry_minutes from this channel.

        Bulk-deletes where possible (< 14 days), single-deletes for older messages
        (capped at single_delete_limit per call — remainder picked up next run).
        Hikari handles rate limiting automatically. Returns count of deleted messages.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=expiry_minutes)
        bulk_cutoff = now - timedelta(days=14)
        to_bulk: list[hikari.Snowflake] = []
        to_single: list[tuple[hikari.Snowflake, datetime]] = []

        try:
            async for msg in self.bot.rest.fetch_messages(
                channel=self.channel_id, before=cutoff
            ):
                if msg.is_pinned:
                    continue
                if msg.created_at > bulk_cutoff:
                    to_bulk.append(msg.id)
                else:
                    to_single.append((msg.id, msg.created_at))
        except (hikari.ForbiddenError, hikari.NotFoundError) as exc:
            self.logger.warning(
                "Cannot fetch messages for cleanup",
                channel=self.channel_name,
                error=str(exc),
            )
            return 0

        for i in range(0, len(to_bulk), 100):
            try:
                await self.bot.rest.delete_messages(
                    self.channel_id, to_bulk[i : i + 100]
                )
            except (hikari.ForbiddenError, hikari.NotFoundError) as exc:
                self.logger.warning(
                    "Bulk delete failed",
                    channel=self.channel_name,
                    error=str(exc),
                )
                break

        to_single = to_single[:single_delete_limit]

        for idx, (msg_id, _) in enumerate(to_single):
            if idx > 0 and idx % 10 == 0:
                self.logger.debug(
                    "Single-deleting old messages, progress",
                    channel=self.channel_name,
                    deleted_so_far=idx,
                    total=len(to_single),
                )
            try:
                await self.bot.rest.delete_message(
                    channel=self.channel_id, message=msg_id
                )
            except hikari.NotFoundError:
                pass  # Already gone
            except hikari.ForbiddenError as exc:
                self.logger.warning(
                    "Single delete failed, stopping",
                    channel=self.channel_name,
                    error=str(exc),
                )
                break

        if to_single:
            oldest_age_days = (now - to_single[-1][1]).days
            self.logger.debug(
                "Single-delete complete",
                channel=self.channel_name,
                count=len(to_single),
                oldest_days=oldest_age_days,
            )

        return len(to_bulk) + len(to_single)

    async def delete_my_messages(self) -> None:
        """Delete all bot messages from this channel."""
        self.logger.debug(
            "Checking for old messages in channel", channel_id=self.channel_id
        )
        assert self.bot.user_id
        async for message in self.bot.rest.fetch_messages(channel=self.channel_id):
            if message.author.id == self.bot.user_id:
                self.logger.debug("Deleting my message", message_id=message.id)
                await message.delete()

    async def check_perms(self) -> list[str]:
        """Return list of missing permission names for the bot in this channel."""
        return await check_channel_perms(self.bot, self.guild_id, self.channel_id)


# ---------------------------------------------------------------------------- #
#                           Permission helpers                                 #
# ---------------------------------------------------------------------------- #


def member_has_role(member: hikari.Member, role_name: str) -> bool:
    """Check if a member has a role by name (via the guild's role cache)."""
    for role in member.get_roles():
        if role.name == role_name:
            return True
    return False


def has_permission(
    guild: hikari.Guild | hikari.RESTGuild,
    member: hikari.Member,
    role_name: str | None,
) -> bool:
    """Check if member has the named role, or is the server (guild) owner.

    When role_name is None, only the guild owner passes (owner-only access).
    """
    if member.id == guild.owner_id:
        return True
    if role_name:
        return member_has_role(member, role_name)
    return False


def has_any_role_permission(
    guild: hikari.Guild | hikari.RESTGuild,
    member: hikari.Member,
    role_names: list[str],
) -> bool:
    """Check if member has any of the named roles, or is the guild owner.

    When role_names is empty, only the guild owner passes (owner-only access).
    """
    if member.id == guild.owner_id:
        return True
    for name in role_names:
        if member_has_role(member, name):
            return True
    return False


PERM_LABELS: dict[hikari.Permissions, str] = {
    hikari.Permissions.SEND_MESSAGES: "Send Messages",
    hikari.Permissions.EMBED_LINKS: "Embed Links",
    hikari.Permissions.ATTACH_FILES: "Attach Files",
}


async def check_channel_perms(
    bot: DragonpawBot, guild_id: hikari.Snowflake, channel_id: hikari.Snowflake
) -> list[str]:
    """Return a list of missing permission names for the bot in the given channel."""
    logger.debug(
        "Checking bot permissions in channel",
        channel_id=channel_id,
        guild_id=guild_id,
    )
    assert bot.user_id
    me = await bot.rest.fetch_member(guild_id, bot.user_id)
    roles = await bot.rest.fetch_roles(guild_id)
    role_map = {r.id: r for r in roles}

    # Start with @everyone permissions
    everyone_role = role_map.get(guild_id)
    perms = everyone_role.permissions if everyone_role else hikari.Permissions.NONE

    # Add permissions from member's roles
    for role_id in me.role_ids:
        role = role_map.get(role_id)
        if role:
            perms |= role.permissions

    # Administrator bypasses everything
    if perms & hikari.Permissions.ADMINISTRATOR:
        return []

    # Apply channel permission overwrites
    try:
        channel = await bot.rest.fetch_channel(channel_id)
    except hikari.ForbiddenError:
        logger.warning(
            "Cannot fetch channel — bot lacks View Channel permission",
            channel_id=channel_id,
            guild_id=guild_id,
        )
        return ["View Channel (cannot access channel)"]
    except hikari.NotFoundError:
        logger.warning(
            "Channel no longer exists", channel_id=channel_id, guild_id=guild_id
        )
        return ["Channel not found (may have been deleted)"]
    if isinstance(channel, hikari.PermissibleGuildChannel):
        overwrites = channel.permission_overwrites
        # @everyone overwrite
        if guild_id in overwrites:
            ow = overwrites[guild_id]
            perms &= ~ow.deny
            perms |= ow.allow
        # Role overwrites
        allow = hikari.Permissions.NONE
        deny = hikari.Permissions.NONE
        for role_id in me.role_ids:
            if role_id in overwrites:
                ow = overwrites[role_id]
                allow |= ow.allow
                deny |= ow.deny
        perms &= ~deny
        perms |= allow
        # Member-specific overwrite
        if me.id in overwrites:
            ow = overwrites[me.id]
            perms &= ~ow.deny
            perms |= ow.allow

    missing = []
    for perm, label in PERM_LABELS.items():
        if not (perms & perm):
            missing.append(label)
    return missing
