from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from dragonpaw_bot import journal
from dragonpaw_bot.plugins.journal import commands as journal_commands


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(journal.store, "state_dir", tmp_path)
    journal.store.cache.clear()
    return journal.store


def _record(kind="warning", user_id=7, summary="thing happened", detail=None):
    return journal.record(
        1,
        "Guild",
        user_id=user_id,
        user_name="Vee",
        kind=kind,
        summary=summary,
        detail=detail,
    )


def test_ids_are_monotonic(store):
    assert [_record().id for _ in range(3)] == [1, 2, 3]


def test_ids_survive_a_reload(store):
    _record()
    store.cache.clear()
    assert _record().id == 2


def test_entries_for_is_newest_first(store):
    a, b = _record(summary="older"), _record(summary="newer")
    got = journal.entries_for(1, 7)
    assert [e.id for e in got] == [b.id, a.id]


def test_entries_for_filters_by_user(store):
    _record(user_id=7)
    _record(user_id=8)
    assert [e.user_id for e in journal.entries_for(1, 8)] == [8]


def test_no_entries_means_eligible(store):
    assert journal.is_ineligible(1, 7) is False


def test_warnings_alone_do_not_affect_eligibility(store):
    _record(kind="warning")
    assert journal.is_ineligible(1, 7) is False


def test_latest_eligibility_entry_wins(store):
    _record(kind="ineligible")
    assert journal.is_ineligible(1, 7) is True
    _record(kind="eligible")
    assert journal.is_ineligible(1, 7) is False
    _record(kind="ineligible")
    assert journal.is_ineligible(1, 7) is True


def test_ineligible_user_ids_lists_only_current(store):
    _record(kind="ineligible", user_id=7)
    _record(kind="ineligible", user_id=8)
    _record(kind="eligible", user_id=8)
    assert journal.ineligible_user_ids(1) == [7]


def test_round_trip_preserves_detail_and_follow_ups(store):
    detail = journal.WarningDetail(
        reason="long reason",
        issuer_id=99,
        issuer_name="Staffy",
        evidence_url="https://discord.com/channels/1/2/3",
        evidence_text="the bad message",
    )
    entry = _record(detail=detail)
    journal.add_follow_up(
        1, entry.id, author_id=99, author_name="Staffy", text="resolved"
    )

    store.cache.clear()
    loaded = journal.entry_by_id(1, entry.id)
    assert loaded is not None
    assert loaded.detail is not None
    assert loaded.detail.reason == "long reason"
    assert loaded.detail.evidence_text == "the bad message"
    assert [f.text for f in loaded.detail.follow_ups] == ["resolved"]


def test_follow_up_on_observed_entry_is_rejected(store):
    entry = _record(kind="ticket_opened", detail=None)
    assert (
        journal.add_follow_up(
            1, entry.id, author_id=99, author_name="Staffy", text="nope"
        )
        is None
    )


def test_follow_up_on_missing_entry_is_rejected(store):
    assert (
        journal.add_follow_up(1, 999, author_id=99, author_name="Staffy", text="nope")
        is None
    )


def test_created_at_is_timezone_aware(store):
    entry = _record()
    assert entry.created_at.tzinfo is not None
    assert entry.created_at <= datetime.now(UTC)


def test_staff_role_persists(store):
    st = journal.load(1)
    st.staff_role_id = 555
    journal.save(st)
    store.cache.clear()
    assert journal.load(1).staff_role_id == 555


def _ctx(role_ids=()):
    ctx = MagicMock()
    ctx.member.role_ids = list(role_ids)
    return ctx


def test_staff_gate_blocks_when_no_role_configured():
    blocked = journal_commands.staff_blocked(_ctx([555]), None)
    assert blocked is not None
    assert "config journal set" in blocked


def test_staff_gate_blocks_a_non_staff_member():
    assert journal_commands.staff_blocked(_ctx([111]), 555) is not None


def test_staff_gate_blocks_when_there_is_no_member():
    ctx = MagicMock()
    ctx.member = None
    assert journal_commands.staff_blocked(ctx, 555) is not None


def test_staff_gate_allows_a_staff_member():
    assert journal_commands.staff_blocked(_ctx([111, 555]), 555) is None


def test_render_entry_leads_with_kind_emoji(store):
    entry = _record(kind="warning", summary="was rude")
    line = journal_commands.render_entry(entry)
    assert line.startswith("⚠️")
    assert "was rude" in line
    assert f"#{entry.id}" in line


def test_render_entry_nests_follow_ups(store):
    detail = journal.WarningDetail(reason="was rude", issuer_id=9, issuer_name="Staffy")
    created = _record(kind="warning", detail=detail)
    journal.add_follow_up(
        1, created.id, author_id=9, author_name="Staffy", text="retracted, my error"
    )
    entry = journal.entry_by_id(1, created.id)
    assert entry is not None

    lines = journal_commands.render_entry(entry).splitlines()
    assert len(lines) == 2
    assert "↳" in lines[1]
    assert "retracted, my error" in lines[1]
    assert "Staffy" in lines[1]


def test_render_entry_shows_evidence_link(store):
    detail = journal.WarningDetail(
        reason="was rude",
        issuer_id=9,
        issuer_name="Staffy",
        evidence_url="https://discord.com/channels/1/2/3",
    )
    entry = _record(kind="warning", detail=detail)
    assert "https://discord.com/channels/1/2/3" in journal_commands.render_entry(entry)


def test_render_timeline_is_newest_first(store):
    _record(summary="older")
    _record(summary="newer")
    text = journal_commands.render_timeline(journal.entries_for(1, 7))
    assert text.index("newer") < text.index("older")


def test_render_timeline_handles_empty(store):
    assert "nothing" in journal_commands.render_timeline([]).lower()


def test_render_timeline_truncates_instead_of_exploding():
    entries = [
        journal.JournalEntry(
            id=i,
            user_id=7,
            user_name="Vee",
            kind="note",
            created_at=datetime.now(UTC),
            summary=f"entry number {i} with some padding text to bulk it out",
        )
        for i in range(400)
    ]
    text = journal_commands.render_timeline(entries)
    assert len(text) <= 4096
    assert "older entries omitted" in text
