"""Automatic lurker purge: candidate selection and schedule decisions."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Literal

from dragonpaw_bot.plugins.activity.models import (
    PURGE_BOOT_EXPIRY_DAYS,
    ActivityGuildMeta,
    PurgeCandidate,
    add_months,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    import hikari

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
