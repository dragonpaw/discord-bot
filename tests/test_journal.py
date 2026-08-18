from datetime import UTC, datetime

import pytest

from dragonpaw_bot import journal


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
