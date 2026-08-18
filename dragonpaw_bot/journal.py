"""Per-guild member journal — the shared record plugins write into.

Lives in core rather than under plugins/ because gc.log() writes to it;
plugins/journal/ owns only the command surface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import pydantic
import structlog

from dragonpaw_bot.state_store import GuildStateBase, GuildStateStore

logger = structlog.get_logger(__name__)

EntryKind = Literal[
    "note",
    "warning",
    "ineligible",
    "eligible",
    "ticket_opened",
    "birthday_set",
    "birthday_removed",
    "name_change",
]

#: An embed has one colour, so kinds are distinguished by leading emoji instead.
KIND_EMOJI: dict[str, str] = {
    "note": "📝",
    "warning": "⚠️",
    "ineligible": "🚫",
    "eligible": "✅",
    "ticket_opened": "🎫",
    "birthday_set": "🎂",
    "birthday_removed": "🎂",
    "name_change": "🏷️",
}


class FollowUp(pydantic.BaseModel):
    author_id: int
    author_name: str
    created_at: datetime
    text: str


class WarningDetail(pydantic.BaseModel):
    reason: str
    issuer_id: int
    issuer_name: str
    evidence_url: str | None = None
    evidence_text: str | None = None
    follow_ups: list[FollowUp] = pydantic.Field(default_factory=list)


class JournalEntry(pydantic.BaseModel):
    id: int
    user_id: int
    user_name: str
    kind: EntryKind
    created_at: datetime
    summary: str
    detail: WarningDetail | None = None


class JournalGuildState(GuildStateBase):
    staff_role_id: int | None = None
    next_id: int = 1
    entries: list[JournalEntry] = pydantic.Field(default_factory=list)


store = GuildStateStore("journal", JournalGuildState)
load = store.load
save = store.save


def record(  # noqa: PLR0913
    guild_id: int,
    guild_name: str,
    *,
    user_id: int,
    user_name: str,
    kind: EntryKind,
    summary: str,
    detail: WarningDetail | None = None,
) -> JournalEntry:
    """Append an entry and persist it."""
    st = load(guild_id)
    st.guild_name = guild_name
    entry = JournalEntry(
        id=st.next_id,
        user_id=user_id,
        user_name=user_name,
        kind=kind,
        created_at=datetime.now(UTC),
        summary=summary,
        detail=detail,
    )
    st.next_id += 1
    st.entries.append(entry)
    save(st)
    logger.info(
        "Journal entry recorded",
        guild=guild_name,
        user=user_name,
        kind=kind,
        entry_id=entry.id,
    )
    return entry


def entries_for(guild_id: int, user_id: int) -> list[JournalEntry]:
    """A member's entries, newest first."""
    return sorted(
        (e for e in load(guild_id).entries if e.user_id == user_id),
        key=lambda e: (e.created_at, e.id),
        reverse=True,
    )


def entry_by_id(guild_id: int, entry_id: int) -> JournalEntry | None:
    return next((e for e in load(guild_id).entries if e.id == entry_id), None)


def is_ineligible(guild_id: int, user_id: int) -> bool:
    """Latest ineligible/eligible entry wins; no such entry means eligible."""
    for entry in entries_for(guild_id, user_id):
        if entry.kind in ("ineligible", "eligible"):
            return entry.kind == "ineligible"
    return False


def ineligible_user_ids(guild_id: int) -> list[int]:
    user_ids = {e.user_id for e in load(guild_id).entries}
    return sorted(uid for uid in user_ids if is_ineligible(guild_id, uid))


def add_follow_up(
    guild_id: int,
    entry_id: int,
    *,
    author_id: int,
    author_name: str,
    text: str,
) -> JournalEntry | None:
    """Append a follow-up. Returns None if the entry is missing or not authored."""
    st = load(guild_id)
    entry = next((e for e in st.entries if e.id == entry_id), None)
    if entry is None or entry.detail is None:
        return None
    entry.detail.follow_ups.append(
        FollowUp(
            author_id=author_id,
            author_name=author_name,
            created_at=datetime.now(UTC),
            text=text,
        )
    )
    save(st)
    logger.info("Journal follow-up added", guild=st.guild_name, entry_id=entry_id)
    return entry
