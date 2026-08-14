"""The button channel: one embed-and-button card per plugin, all in one place.

Members can't be expected to remember slash commands, so each plugin declares a
``BUTTON_ENTRY`` in its package ``__init__``; this module collects them, renders
them into a configured channel, and owns ``/config buttons``.

It never learns what a button *does* — clicks route through bot.py's interaction
dispatcher back to the plugin that declared the custom_id.
"""

from __future__ import annotations

import contextlib
import datetime

import hikari
import lightbulb
import structlog

from dragonpaw_bot import structs
from dragonpaw_bot.context import (
    CHANNEL_CLEANUP_PERMS,
    ChannelContext,
    GuildContext,
    guild_owner_only,
)
from dragonpaw_bot.plugins import activity, birthdays, subday, tickets

logger = structlog.get_logger(__name__)

# Display order. Tickets first — asking for help is the most time-sensitive
# thing anyone comes here to do.
_ENTRIES: list[structs.ButtonEntry] = [
    tickets.BUTTON_ENTRY,
    subday.BUTTON_ENTRY,
    birthdays.BUTTON_ENTRY,
    activity.BUTTON_ENTRY,
]

_EMPTY_NOTE = (
    "*curls up in the empty channel* 🐉 Nothing to show here just yet! "
    "Once staff switch on a few of my features, their buttons will pop up "
    "right here. 🐾"
)


def available_entries(guild_id: int) -> list[structs.ButtonEntry]:
    """The entries whose plugins are configured for this guild."""
    return [entry for entry in _ENTRIES if entry.is_available(guild_id)]


def _build_embed(entry: structs.ButtonEntry) -> hikari.Embed:
    return hikari.Embed(
        title=entry.title,
        description=entry.description,
        color=entry.color,
    )


def _build_row(entry: structs.ButtonEntry) -> hikari.api.MessageActionRowBuilder:
    row = hikari.impl.MessageActionRowBuilder()
    for button in entry.buttons:
        row.add_interactive_button(
            hikari.ButtonStyle.PRIMARY,
            button.custom_id,
            label=button.label,
            emoji=button.emoji,
        )
    return row


def _channel_context(gc: GuildContext, channel_id: hikari.Snowflake) -> ChannelContext:
    channel = gc.bot.cache.get_guild_channel(channel_id)
    name = (channel.name if channel else None) or str(channel_id)
    return ChannelContext.from_channel(gc, int(channel_id), name)


async def post_buttons(gc: GuildContext) -> list[str]:
    """Wipe the guild's button channel and repost every available card.

    Returns human-readable warnings for the admin; empty means all went well.
    """
    state = gc.state()
    if not state or not state.button_channel_id:
        return ["No button channel is set — pick one with `/config buttons channel`."]

    channel_id = state.button_channel_id
    cc = _channel_context(gc, channel_id)

    missing = await cc.check_perms(CHANNEL_CLEANUP_PERMS)
    if missing:
        gc.logger.warning(
            "Missing permissions for button channel",
            channel=cc.channel_name,
            missing=missing,
        )
        return [
            f"I'm missing **{', '.join(missing)}** in <#{channel_id}> — "
            "I can't tidy up or post my buttons there until that's sorted."
        ]

    entries = available_entries(int(gc.guild_id))
    gc.logger.info(
        "Posting button channel",
        channel=cc.channel_name,
        entries=[e.key for e in entries],
    )

    try:
        await cc.delete_my_messages()

        if not entries:
            await gc.bot.rest.create_message(channel=channel_id, content=_EMPTY_NOTE)
            return [
                "None of my button-having features are configured yet, so there's "
                "nothing to post."
            ]

        for entry in entries:
            await gc.bot.rest.create_message(
                channel=channel_id,
                embed=_build_embed(entry),
                component=_build_row(entry),
            )
    except hikari.HTTPError as exc:
        gc.logger.exception("Failed to post button channel", channel=cc.channel_name)
        await gc.log(
            f"🤯 *tangles wings* I tried to set up my buttons in <#{channel_id}> "
            "and tripped over something — check the bot logs! 🐉"
        )
        return [f"I hit an error posting to <#{channel_id}>: {exc}"]

    return []


def _ensure_state(gc: GuildContext, guild: hikari.Guild) -> structs.GuildState:
    """The guild's state, freshly minted if this is the first thing it configures."""
    state = gc.state()
    if state:
        return state
    return structs.GuildState(
        id=gc.guild_id,
        name=guild.name,
        config_url="",
        config_last=datetime.datetime.now(tz=datetime.UTC),
    )


def _warning_text(warnings: list[str]) -> str:
    return "\n".join(f"- {w}" for w in warnings)


class ButtonsChannel(
    lightbulb.SlashCommand,
    name="channel",
    description="Set or clear the channel where my feature buttons live.",
    hooks=[guild_owner_only],
):
    channel = lightbulb.channel(
        "channel", "Channel to host the buttons (omit to clear)", default=None
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            logger.error("Interaction without a guild")
            return

        gc = GuildContext.from_ctx(ctx)
        guild = await gc.fetch_guild()
        state = _ensure_state(gc, guild)
        actor = ctx.member.display_name if ctx.member else ctx.user.display_name

        if self.channel is None:
            old_channel_id = state.button_channel_id
            state.button_channel_id = None
            gc.bot.state_update(state)
            gc.logger.info("Cleared button channel")
            await ctx.respond(
                "Button channel cleared.", flags=hikari.MessageFlag.EPHEMERAL
            )
            if old_channel_id:
                cc = _channel_context(gc, old_channel_id)
                with contextlib.suppress(hikari.HTTPError):
                    await cc.delete_my_messages()
            await gc.log(
                f"🧹 **{actor}** put away my button channel — I've tidied up after "
                "myself! 🐾"
            )
            return

        state.button_channel_id = self.channel.id
        gc.bot.state_update(state)
        gc.logger.info("Set button channel", channel=self.channel.name)
        await ctx.respond(
            f"Button channel set to <#{self.channel.id}> — putting my buttons out now! 🐉",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        warnings = await post_buttons(gc)
        if warnings:
            await ctx.respond(
                f"⚠️ **Saved, but:**\n{_warning_text(warnings)}",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
        await gc.log(
            f"🔘 **{actor}** made <#{self.channel.id}> my button channel — "
            "*proudly arranges buttons* 🐾"
        )


class ButtonsRefresh(
    lightbulb.SlashCommand,
    name="refresh",
    description="Repost my feature buttons, picking up any new features.",
    hooks=[guild_owner_only],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            logger.error("Interaction without a guild")
            return

        gc = GuildContext.from_ctx(ctx)
        warnings = await post_buttons(gc)

        if warnings:
            await ctx.respond(
                f"⚠️ **Reposted with warnings:**\n{_warning_text(warnings)}",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        await ctx.respond(
            "*happy tail wag* All freshened up! 🐉",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


class ButtonsStatus(
    lightbulb.SlashCommand,
    name="status",
    description="Show the button channel and which buttons are showing.",
    hooks=[guild_owner_only],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            logger.error("Interaction without a guild")
            return

        gc = GuildContext.from_ctx(ctx)
        state = gc.state()
        channel_id = state.button_channel_id if state else None

        lines = [
            f"**Channel:** <#{channel_id}>"
            if channel_id
            else "**Channel:** not set — use `/config buttons channel`"
        ]
        guild_id = int(ctx.guild_id)
        for entry in _ENTRIES:
            mark = (
                "✅ showing"
                if entry.is_available(guild_id)
                else "➖ hidden (not configured)"
            )
            lines.append(f"{mark} — **{entry.title}**")

        await ctx.respond("\n".join(lines), flags=hikari.MessageFlag.EPHEMERAL)


def register(subgroup: lightbulb.SubGroup) -> None:
    subgroup.register(ButtonsChannel)
    subgroup.register(ButtonsRefresh)
    subgroup.register(ButtonsStatus)
