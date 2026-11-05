"""Automatic lurker purge: candidate selection, schedule decisions, staff posts."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Literal

import hikari
import structlog

from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.models import (
    PURGE_BOOT_EXPIRY_DAYS,
    ActivityGuildMeta,
    LurkerPurge,
    PurgeCandidate,
    add_months,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from dragonpaw_bot.context import GuildContext

logger = structlog.get_logger(__name__)

SPARE_ID = "activity_purge_spare"
BOOT_ID = "activity_purge_boot"
MAX_MENU_OPTIONS = 25

PurgeAction = Literal["start", "remind", "final", "expire"]


def select_candidates(
    members: Iterable[hikari.Member], lurker_role_id: int
) -> list[PurgeCandidate]:
    candidates = [
        PurgeCandidate(user_id=m.id, display_name=m.display_name)
        for m in members
        if not m.is_bot and lurker_role_id in m.role_ids
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


def build_purge_post(
    bot: hikari.GatewayBot, purge: LurkerPurge, grace_days: int
) -> tuple[hikari.Embed, list[hikari.api.ComponentBuilder]]:
    if purge.boot_posted_at is not None:
        title = "🥢 The final list is ready!"
    else:
        days_left = grace_days - purge.day + 1
        title = f"🍽️ Lurker purge — {days_left} day(s) left to spare someone"
    lines = [
        f"~~{c.display_name}~~ — spared by {c.spared_by} 💜"
        if c.spared_by
        else c.display_name
        for c in purge.candidates
    ]
    embed = hikari.Embed(title=title, description="\n".join(lines))

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


async def _post(gc: GuildContext, meta: ActivityGuildMeta, purge: LurkerPurge) -> None:
    if gc.log_channel_id is None:
        logger.warning("No log channel for lurker purge post", guild=meta.guild_name)
        return
    if purge.message_id is not None:
        try:
            await gc.bot.rest.edit_message(
                gc.log_channel_id, purge.message_id, components=[]
            )
        except hikari.NotFoundError:
            logger.debug("Old purge post already gone", guild=meta.guild_name)
    embed, components = build_purge_post(gc.bot, purge, meta.config.purge_grace_days)
    message = await gc.bot.rest.create_message(
        gc.log_channel_id, embed=embed, components=components
    )
    purge.message_id = int(message.id)


async def start_purge(
    gc: GuildContext,
    meta: ActivityGuildMeta,
    members: Iterable[hikari.Member],
    now: datetime.datetime,
) -> None:
    meta.last_purge_started = now
    candidates = select_candidates(members, meta.config.lurker_role_id or 0)
    if not candidates:
        await gc.log(
            "🌟 *happy tail wag* It's purge time, but there are no lurkers to sweep"
            " up — everyone's been lovely and active! 🐉"
        )
        return
    purge = LurkerPurge(started_at=now, candidates=candidates)
    meta.purge = purge
    logger.info("Lurker purge started", guild=meta.guild_name, count=len(candidates))
    await _post(gc, meta, purge)


async def cancel_purge(gc: GuildContext, meta: ActivityGuildMeta) -> None:
    purge = meta.purge
    if purge is None:
        return
    if purge.message_id is not None and gc.log_channel_id is not None:
        try:
            await gc.bot.rest.edit_message(
                gc.log_channel_id, purge.message_id, components=[]
            )
        except hikari.NotFoundError:
            logger.debug("Old purge post already gone", guild=meta.guild_name)
    meta.purge = None
    logger.info("Lurker purge cancelled", guild=meta.guild_name)
    await gc.log(
        "🕊️ *tucks my tail* I've called off the lurker purge — nobody is getting"
        " booted. 🐾"
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
        await start_purge(gc, meta, members, now)
    elif action in ("remind", "final") and purge is not None:
        if action == "remind":
            purge.day += 1
        else:
            purge.boot_posted_at = now
        await _post(gc, meta, purge)
    elif action == "expire":
        meta.purge = None
        logger.info("Lurker purge expired", guild=meta.guild_name)
        await gc.log(
            "⌛ *yawns* Nobody pressed Boot, so this lurker purge has lapsed and"
            " nobody was kicked. I'll be back with the next one on schedule! 🐉"
        )
    else:
        return
    activity_state.save_config(meta)
