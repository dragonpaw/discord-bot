from datetime import UTC, datetime
from typing import get_args
from unittest.mock import MagicMock

import pytest

import dragonpaw_bot.bot as bot_module
from dragonpaw_bot import journal
from dragonpaw_bot.plugins.journal import MODAL_HANDLERS
from dragonpaw_bot.plugins.journal import commands as journal_commands
from dragonpaw_bot.plugins.journal import listeners as journal_listeners


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


def test_summarize_keeps_short_reasons_whole():
    assert journal_commands.summarize("was rude in general") == "was rude in general"


def test_summarize_collapses_newlines():
    assert "\n" not in journal_commands.summarize("line one\nline two")


def test_summarize_truncates_long_reasons():
    out = journal_commands.summarize("x" * 500)
    assert len(out) <= journal_commands.SUMMARY_LIMIT
    assert out.endswith("…")


def test_add_modal_prefix_is_routed():
    assert journal_commands.ADD_MODAL_PREFIX in MODAL_HANDLERS


def test_add_modal_custom_id_fits_discord_limit():
    cid = f"{journal_commands.ADD_MODAL_PREFIX}999999999999999999:ineligible"
    assert len(cid) <= 100


def test_followup_modal_prefix_is_routed():
    assert journal_commands.FOLLOWUP_MODAL_PREFIX in MODAL_HANDLERS


def test_modal_prefixes_are_unambiguous():
    prefixes = list(MODAL_HANDLERS)
    for a in prefixes:
        for b in prefixes:
            if a != b:
                assert not a.startswith(b), f"{a!r} shadows {b!r} in prefix routing"


def test_jump_url_format():
    assert journal_commands.jump_url(1, 2, 3) == "https://discord.com/channels/1/2/3"


def test_warn_message_modal_prefix_is_routed():
    assert journal_commands.WARN_MSG_MODAL_PREFIX in MODAL_HANDLERS


def test_warn_message_custom_id_fits_discord_limit():
    cid = journal_commands.WARN_MSG_MODAL_PREFIX + ":".join(["999999999999999999"] * 3)
    assert len(cid) <= 100


class _NamedMember:
    def __init__(self, display_name):
        self.display_name = display_name


def test_delta_detected_on_rename():
    assert journal_listeners.display_name_change(
        _NamedMember("Old"), _NamedMember("New")
    ) == ("Old", "New")


def test_no_delta_when_name_unchanged():
    assert (
        journal_listeners.display_name_change(
            _NamedMember("Same"), _NamedMember("Same")
        )
        is None
    )


def test_no_delta_when_old_member_uncached():
    assert journal_listeners.display_name_change(None, _NamedMember("New")) is None


def test_every_entry_kind_has_an_emoji():
    for kind in get_args(journal.EntryKind):
        assert kind in journal.KIND_EMOJI, f"{kind} has no emoji"


def test_modal_responding_commands_are_excluded_from_auto_defer():
    """A modal cannot follow a deferred response, so these must opt out."""
    qualified = {
        sub._command_data.qualified_name
        for sub in journal_commands.journal_group._commands.values()
    }
    qualified.add(
        journal_commands.LogWarningMessageCommand._command_data.qualified_name
    )

    modal_commands = {"journal add", "journal followup", "Log warning"}
    assert modal_commands <= qualified, "command names drifted from the exclusion list"
    assert modal_commands <= bot_module._AUTO_DEFER_EXCLUSIONS


def test_modal_title_fits_discord_cap():
    long_name = "x" * 32
    title = journal_commands.modal_title("Journal entry", long_name)
    assert len(title) <= journal_commands.MODAL_TITLE_LIMIT
    assert title.endswith("…")


def test_modal_title_left_alone_when_short():
    assert journal_commands.modal_title("Warning", "Vee") == "Warning — Vee"


def test_evidence_renders_as_blockquote(store):
    detail = journal.WarningDetail(
        reason="was rude",
        issuer_id=9,
        issuer_name="Staffy",
        evidence_text="the bad message",
    )
    entry = _record(kind="warning", detail=detail)
    lines = journal_commands.render_entry(entry).splitlines()
    assert lines[1] == "> the bad message"


def test_evidence_collapses_newlines_into_one_quote_line():
    # Every line would need its own '>'; flattening avoids a broken quote block.
    out = journal_commands.render_evidence("line one\nline two")
    assert out == "> line one line two"
    assert "\n" not in out


def test_evidence_is_truncated():
    out = journal_commands.render_evidence("x" * 900)
    assert len(out) <= journal_commands.EVIDENCE_PREVIEW_LIMIT + 2
    assert out.endswith("…")


def test_evidence_precedes_follow_ups(store):
    detail = journal.WarningDetail(
        reason="was rude", issuer_id=9, issuer_name="Staffy", evidence_text="cited text"
    )
    created = _record(kind="warning", detail=detail)
    journal.add_follow_up(
        1, created.id, author_id=9, author_name="Staffy", text="resolved"
    )
    entry = journal.entry_by_id(1, created.id)
    assert entry is not None
    lines = journal_commands.render_entry(entry).splitlines()
    assert lines[1].startswith(">")
    assert "↳" in lines[2]


def test_oversized_newest_entry_still_shows_something():
    """Bailing on the first entry would render only the omission notice."""
    entries = [
        journal.JournalEntry(
            id=1,
            user_id=7,
            user_name="Vee",
            kind="note",
            created_at=datetime.now(UTC),
            summary="y" * 6000,
        )
    ]
    text = journal_commands.render_timeline(entries)
    assert len(text) <= 4096
    assert "yyyy" in text, "expected a truncated entry, not just the notice"


def _member_update_event(guild_id, old_name, new_name):
    event = MagicMock()
    event.guild_id = guild_id
    event.old_member = _NamedMember(old_name)
    event.member = MagicMock()
    event.member.id = 7
    event.member.display_name = new_name
    guild = MagicMock()
    guild.name = "Guild"  # MagicMock(name=...) sets the repr, not .name
    event.app.cache.get_guild.return_value = guild
    return event


async def test_name_change_skipped_when_journal_unconfigured(store):
    """No staff role means nobody could ever read the entry."""
    await journal_listeners.on_member_update(_member_update_event(1, "Old", "New"))
    assert journal.load(1).entries == []


async def test_name_change_recorded_when_configured(store):
    st = journal.load(1)
    st.staff_role_id = 555
    journal.save(st)

    await journal_listeners.on_member_update(_member_update_event(1, "Old", "New"))
    entries = journal.entries_for(1, 7)
    assert [e.kind for e in entries] == ["name_change"]
    assert "Old" in entries[0].summary and "New" in entries[0].summary


async def test_name_change_ignores_non_rename_updates(store):
    st = journal.load(1)
    st.staff_role_id = 555
    journal.save(st)

    await journal_listeners.on_member_update(_member_update_event(1, "Same", "Same"))
    assert journal.load(1).entries == []


def test_escape_markdown_neutralises_formatting():
    assert journal.escape_markdown("**bold**") == "\\*\\*bold\\*\\*"
    assert journal.escape_markdown("a_b~c`d|e") == "a\\_b\\~c\\`d\\|e"


def test_escape_markdown_leaves_plain_text_alone():
    assert journal.escape_markdown("just a normal message") == "just a normal message"


def test_evidence_cannot_forge_attribution(store):
    """A member's own message must not be able to fake a staff byline."""
    detail = journal.WarningDetail(
        reason="r",
        issuer_id=9,
        issuer_name="Staffy",
        evidence_text="nice *(by Admin)* nonsense **bold**",
    )
    entry = _record(kind="warning", detail=detail)
    quote = journal_commands.render_entry(entry).splitlines()[1]
    assert "**bold**" not in quote
    assert "\\*\\*bold\\*\\*" in quote


async def test_name_change_summary_escapes_nickname(store):
    st = journal.load(1)
    st.staff_role_id = 555
    journal.save(st)

    await journal_listeners.on_member_update(
        _member_update_event(1, "Old", "Evil** *(by Admin)*")
    )
    summary = journal.entries_for(1, 7)[0].summary
    assert "Evil** *(by Admin)*" not in summary
    assert "\\*\\*" in summary
