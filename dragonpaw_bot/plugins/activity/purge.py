"""Automatic lurker purge: candidate selection, schedule decisions, staff posts."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Literal

import hikari
import structlog

from dragonpaw_bot import journal
from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.models import (
    PURGE_BOOT_EXPIRY_DAYS,
    ActivityGuildMeta,
    LurkerPurge,
    PurgeCandidate,
    add_months,
    is_newcomer,
)
from dragonpaw_bot.utils import guild_member

if TYPE_CHECKING:
    from collections.abc import Iterable

    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

SPARE_ID = "activity_purge_spare"
BOOT_ID = "activity_purge_boot"
MAX_MENU_OPTIONS = 25
MAX_DESCRIPTION = 4096
MAX_MESSAGE = 2000

PurgeAction = Literal["start", "remind", "final", "expire"]
BootOutcome = Literal["kicked", "left", "failed"]


def select_candidates(
    members: Iterable[hikari.Member],
    lurker_role_id: int,
    now: datetime.datetime,
    interval_months: int,
) -> list[PurgeCandidate]:
    candidates = [
        PurgeCandidate(user_id=m.id, display_name=m.display_name)
        for m in members
        if not m.is_bot
        and lurker_role_id in m.role_ids
        and not is_newcomer(m.joined_at, now, interval_months)
    ]
    return sorted(candidates, key=lambda c: c.display_name.casefold())


def next_action(meta: ActivityGuildMeta, now: datetime.datetime) -> PurgeAction | None:
    config = meta.config
    if not config.purge_enabled:
        return None
    purge = meta.purge
    if purge is None:
        last = meta.last_purge_started
        if last is None or now >= add_months(last, config.purge_interval_months):
            return "start"
        return None
    if purge.boot_posted_at is not None:
        expiry = datetime.timedelta(days=PURGE_BOOT_EXPIRY_DAYS)
        return "expire" if now - purge.boot_posted_at >= expiry else None
    if purge.day + 1 <= config.purge_grace_days:
        return "remind"
    return "final"


def _fit_lines(lines: list[str]) -> str:
    shown: list[str] = []
    used = 0
    for i, line in enumerate(lines):
        more = f"…and {len(lines) - i} more"
        reserve = len(more) + 1 if i < len(lines) - 1 else 0
        if used + len(line) + 1 + reserve > MAX_DESCRIPTION:
            shown.append(more)
            break
        shown.append(line)
        used += len(line) + 1
    return "\n".join(shown)


def build_purge_post(
    bot: hikari.GatewayBot, purge: LurkerPurge, grace_days: int
) -> tuple[hikari.Embed, list[hikari.api.ComponentBuilder]]:
    if purge.boot_posted_at is not None:
        title = "🥢 The final list is ready!"
    else:
        days_left = grace_days - purge.day + 1
        title = f"🍽️ Lurker purge — {days_left} day(s) left to spare someone"
    esc = journal.escape_markdown
    lines = [
        f"~~{esc(c.display_name)}~~ — spared by {esc(c.spared_by)} 💜"
        if c.spared_by
        else esc(c.display_name)
        for c in purge.candidates
    ]
    embed = hikari.Embed(title=title, description=_fit_lines(lines))

    components: list[hikari.api.ComponentBuilder] = []
    at_risk = [c for c in purge.candidates if c.spared_by is None]
    if at_risk:
        options = at_risk[:MAX_MENU_OPTIONS]
        menu = (
            bot.rest.build_message_action_row()
            .add_text_menu(SPARE_ID)
            .set_placeholder("Spare someone from the purge")
            .set_max_values(len(options))
        )
        for c in options:
            menu.add_option(c.display_name, str(c.user_id))
        components.append(menu.parent)
    if purge.boot_posted_at is not None:
        components.append(
            bot.rest.build_message_action_row().add_interactive_button(
                hikari.ButtonStyle.DANGER, BOOT_ID, label="Boot"
            )
        )
    return embed, components


async def _strip_components(
    gc: GuildContext, meta: ActivityGuildMeta, purge: LurkerPurge
) -> None:
    channel_id = purge.channel_id or gc.staff_channel_id
    if purge.message_id is None or channel_id is None:
        return
    try:
        await gc.bot.rest.edit_message(channel_id, purge.message_id, components=[])
    except hikari.NotFoundError:
        logger.debug("Old purge post already gone", guild=meta.guild_name)


async def _post(
    gc: GuildContext, meta: ActivityGuildMeta, purge: LurkerPurge, *, ping: bool
) -> None:
    """Repost the list in the staff channel; ping staff for the start and the Boot."""
    channel_id = gc.staff_channel_id
    if channel_id is None:
        logger.warning("No staff channel for lurker purge post", guild=meta.guild_name)
        return
    await _strip_components(gc, meta, purge)
    embed, components = build_purge_post(gc.bot, purge, meta.config.purge_grace_days)
    message = await gc.staff_post(
        embed=embed,
        components=components,
        ping_role_id=meta.config.purge_staff_role_id if ping else None,
    )
    if message is None:
        return
    purge.message_id = int(message.id)
    purge.channel_id = int(channel_id)


async def _staff_note(gc: GuildContext, text: str) -> None:
    """A plain staff-channel note whose loss shouldn't break the caller."""
    try:
        await gc.staff_post(text)
    except hikari.HTTPError as exc:
        gc.logger.warning("Failed to post lurker purge note", error=str(exc))


async def start_purge(
    gc: GuildContext,
    meta: ActivityGuildMeta,
    members: Iterable[hikari.Member],
    now: datetime.datetime,
) -> bool:
    """Start a purge; False (nothing changed) when nobody is on the list."""
    config = meta.config
    candidates = select_candidates(
        members, config.lurker_role_id or 0, now, config.purge_interval_months
    )
    if not candidates:
        logger.debug("No lurkers to purge yet", guild=meta.guild_name)
        return False
    meta.last_purge_started = now
    purge = LurkerPurge(started_at=now, candidates=candidates)
    meta.purge = purge
    logger.info("Lurker purge started", guild=meta.guild_name, count=len(candidates))
    await _post(gc, meta, purge, ping=True)
    return True


async def cancel_purge(gc: GuildContext, meta: ActivityGuildMeta) -> None:
    purge = meta.purge
    if purge is None:
        return
    meta.purge = None
    activity_state.save_config(meta)
    logger.info("Lurker purge cancelled", guild=meta.guild_name)
    await _strip_components(gc, meta, purge)
    await _staff_note(
        gc,
        "🕊️ *tucks my tail* I've called off the lurker purge — nobody is getting"
        " booted. 🐾",
    )


async def purge_daily_step(
    gc: GuildContext,
    meta: ActivityGuildMeta,
    members: Iterable[hikari.Member],
    now: datetime.datetime,
) -> None:
    action = next_action(meta, now)
    purge = meta.purge
    if action == "start":
        if not await start_purge(gc, meta, members, now):
            return
    elif action in ("remind", "final") and purge is not None:
        if action == "remind":
            purge.day += 1
        else:
            purge.boot_posted_at = now
        await _post(gc, meta, purge, ping=action == "final")
    elif action == "expire" and purge is not None:
        meta.purge = None
        activity_state.save_config(meta)
        logger.info("Lurker purge expired", guild=meta.guild_name)
        await _strip_components(gc, meta, purge)
        await _staff_note(
            gc,
            "⌛ *yawns* Nobody pressed Boot, so this lurker purge has lapsed and"
            " nobody was kicked. I'll be back with the next one on schedule! 🐉",
        )
    else:
        return
    activity_state.save_config(meta)


def is_purge_staff(
    meta: ActivityGuildMeta, member: hikari.Member, owner_id: int
) -> bool:
    role_id = meta.config.purge_staff_role_id
    return member.id == owner_id or (
        role_id is not None and hikari.Snowflake(role_id) in member.role_ids
    )


async def _reply(interaction: hikari.ComponentInteraction, content: str) -> None:
    await interaction.create_initial_response(
        hikari.ResponseType.MESSAGE_CREATE,
        content,
        flags=hikari.MessageFlag.EPHEMERAL,
    )


async def handle_spare(interaction: hikari.ComponentInteraction) -> None:
    assert interaction.guild_id is not None
    assert interaction.member is not None
    guild = interaction.get_guild()
    meta = activity_state.load_config(int(interaction.guild_id))
    member = interaction.member
    if guild is None or not is_purge_staff(meta, member, int(guild.owner_id)):
        logger.warning("Lurker purge spare attempted by non-staff")
        await _reply(
            interaction,
            "*snorts smoke* Only the staff team can spare members from the purge! 🐉",
        )
        return

    purge = meta.purge
    if purge is None or interaction.message.id != purge.message_id:
        await _reply(
            interaction,
            "*tilts head* This list isn't live any more, so there's nothing left"
            " to spare. 🐾",
        )
        return

    chosen = {int(v) for v in interaction.values}
    spared = [
        c for c in purge.candidates if c.user_id in chosen and c.spared_by is None
    ]
    for c in spared:
        c.spared_by = member.display_name
    activity_state.save_config(meta)
    logger.info("Members spared from lurker purge", count=len(spared))
    staff = journal.escape_markdown(member.display_name)
    for c in spared:
        journal.record(
            meta.guild_id,
            meta.guild_name,
            user_id=c.user_id,
            user_name=c.display_name,
            kind="lurker_spared",
            summary=f"Spared from the lurker purge by {staff}",
        )

    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    embed, components = build_purge_post(bot, purge, meta.config.purge_grace_days)
    await interaction.create_initial_response(
        hikari.ResponseType.MESSAGE_UPDATE, embed=embed, components=components
    )


GOODBYE_DM = (
    "🐉 *soft dragon nuzzle* Hi there! It's been a long while since I've seen you"
    " around **{guild}**, so the staff team and I are doing a little tidy-up of"
    " quiet members. This isn't a punishment, and there are no hard feelings at"
    " all — thank you for the time you spent with us. I hope all your adventures"
    " are wonderful! 🐾"
)


async def handle_boot(interaction: hikari.ComponentInteraction) -> None:
    assert interaction.guild_id is not None
    assert interaction.member is not None
    guild = interaction.get_guild()
    meta = activity_state.load_config(int(interaction.guild_id))
    staff = interaction.member.display_name
    staff_md = journal.escape_markdown(staff)
    if guild is None or not is_purge_staff(
        meta, interaction.member, int(guild.owner_id)
    ):
        logger.warning("Lurker purge Boot attempted by non-staff")
        await _reply(
            interaction,
            "*snorts smoke* Only the staff team can press Boot on the purge! 🐉",
        )
        return

    purge = meta.purge
    if (
        purge is None
        or purge.boot_posted_at is None
        or interaction.message.id != purge.message_id
    ):
        await _reply(
            interaction,
            "*tilts head* There's no final list waiting for a Boot right now —"
            " it's already been taken care of. 🐾",
        )
        return

    # Claimed before any await, so a second press sees no purge and is turned away.
    meta.purge = None
    activity_state.save_config(meta)
    logger.info("Lurker purge Boot pressed")

    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    embed, _ = build_purge_post(bot, purge, meta.config.purge_grace_days)
    try:
        await interaction.create_initial_response(
            hikari.ResponseType.MESSAGE_UPDATE,
            f"👢 {staff_md} pressed Boot — booting…",
            embed=embed,
            components=[],
        )
    except Exception:
        meta.purge = purge
        activity_state.save_config(meta)
        raise

    outcomes: dict[BootOutcome, list[str]] = {"kicked": [], "left": [], "failed": []}
    for c in purge.candidates:
        if c.spared_by is None:
            outcome = await _boot_one(bot, meta, guild.name, c, staff)
            outcomes[outcome].append(journal.escape_markdown(c.display_name))
    logger.info("Lurker purge finished", **{k: len(v) for k, v in outcomes.items()})
    await _log_boot_summary(
        GuildContext.from_interaction(interaction), staff_md, outcomes
    )


async def _boot_one(
    bot: DragonpawBot,
    meta: ActivityGuildMeta,
    guild_name: str,
    c: PurgeCandidate,
    staff: str,
) -> BootOutcome:
    try:
        member = await guild_member(bot, meta.guild_id, c.user_id)
    except hikari.HTTPError as exc:
        logger.warning(
            "Lurker purge member lookup failed", member=c.display_name, error=str(exc)
        )
        return "failed"
    if member is None:
        return "left"
    try:
        await member.send(GOODBYE_DM.format(guild=guild_name))
    except hikari.HTTPError as exc:
        logger.warning(
            "Could not DM lurker purge goodbye", member=c.display_name, error=str(exc)
        )
    try:
        await bot.rest.kick_user(
            meta.guild_id, c.user_id, reason=f"Lurker purge — booted by {staff}"
        )
    except hikari.NotFoundError:
        return "left"
    except hikari.HTTPError as exc:
        logger.warning(
            "Lurker purge kick failed", member=c.display_name, error=str(exc)
        )
        return "failed"
    journal.record(
        meta.guild_id,
        meta.guild_name,
        user_id=c.user_id,
        user_name=c.display_name,
        kind="lurker_kicked",
        summary=f"Booted in the lurker purge by {journal.escape_markdown(staff)}",
    )
    return "kicked"


async def _log_boot_summary(
    gc: GuildContext, staff_md: str, outcomes: dict[BootOutcome, list[str]]
) -> None:
    sections: list[tuple[str, list[str]]] = []
    if outcomes["failed"]:
        sections += [
            ("⚠️ I couldn't kick:", outcomes["failed"]),
            (
                "🔧 Please check my **Kick Members** permission and that my role"
                " sits above theirs, then kick them by hand. 🐾",
                [],
            ),
        ]
    sections.append(("👢 Booted:", outcomes["kicked"] or ["nobody"]))
    if outcomes["left"]:
        sections.append(("🚪 Had already left:", outcomes["left"]))
    header = (
        f"💨 *happy crunch* {staff_md} pressed Boot and the lurker purge is all"
        " done! 🐉"
    )
    for message in _summary_messages(header, sections):
        await _staff_note(gc, message)


def _summary_messages(header: str, sections: list[tuple[str, list[str]]]) -> list[str]:
    """Pack labelled name lists into messages under Discord's length cap.

    A name is never split; a list that overflows carries on in a new message
    that repeats its label.
    """
    messages = [header]

    def add(text: str, sep: str, restart: str) -> None:
        if len(messages[-1]) + len(sep) + len(text) > MAX_MESSAGE:
            messages.append(restart + text)
        else:
            messages[-1] += sep + text

    for label, names in sections:
        add(label, "\n", "")
        for i, name in enumerate(names):
            add(name, ", " if i else " ", f"{label} ")
    return messages
