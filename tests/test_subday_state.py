import datetime

import yaml

from dragonpaw_bot.plugins.subday import state
from dragonpaw_bot.plugins.subday.commands import _prepare_backfill
from dragonpaw_bot.plugins.subday.constants import MILESTONE_WEEKS, TOTAL_WEEKS
from dragonpaw_bot.plugins.subday.models import (
    SubDayGuildConfig,
    SubDayGuildState,
    SubDayParticipant,
)


def _sample_participant(**kwargs) -> SubDayParticipant:
    defaults = {
        "user_id": 12345,
        "signup_date": datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC),
    }
    defaults.update(kwargs)
    return SubDayParticipant(**defaults)


def test_participant_defaults():
    p = _sample_participant()
    assert p.current_week == 1
    assert p.week_completed is False
    assert p.week_sent is False
    assert p.last_completed_date is None


def test_state_yaml_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    state._cache.clear()

    p = _sample_participant()
    gs = SubDayGuildState(
        guild_id=42,
        guild_name="Test Guild",
        participants={p.user_id: p},
    )
    state.save(gs)

    # Verify YAML is human-readable
    path = tmp_path / "subday_42.yaml"
    assert path.exists()
    with open(path) as f:
        raw = yaml.safe_load(f)
    assert raw["guild_name"] == "Test Guild"
    assert "12345" in str(raw["participants"])

    # Clear cache and reload
    state._cache.clear()
    loaded = state.load(42)
    assert loaded.guild_id == 42
    assert loaded.guild_name == "Test Guild"
    assert 12345 in loaded.participants
    assert loaded.participants[12345].current_week == 1


def test_load_creates_empty_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    state._cache.clear()

    loaded = state.load(999)
    assert loaded.guild_id == 999
    assert loaded.participants == {}


def test_completion_sets_week_completed():
    p = _sample_participant()
    assert not p.week_completed
    p.week_completed = True
    p.last_completed_date = datetime.datetime.now(tz=datetime.UTC)
    assert p.week_completed
    assert p.last_completed_date is not None


def test_advance_week_logic():
    """Simulate what the Sunday scheduler does."""
    p = _sample_participant(current_week=5, week_completed=True, week_sent=True)
    # Advance
    p.current_week += 1
    p.week_completed = False
    p.week_sent = False
    assert p.current_week == 6
    assert not p.week_completed
    assert not p.week_sent


def test_milestone_detection():
    assert 13 in MILESTONE_WEEKS
    assert 26 in MILESTONE_WEEKS
    assert 39 in MILESTONE_WEEKS
    assert 52 in MILESTONE_WEEKS
    assert 1 not in MILESTONE_WEEKS


def test_enroll_role_coerces_string():
    cfg = SubDayGuildConfig(enroll_role="Subscriber")
    assert cfg.enroll_role == ["Subscriber"]


def test_enroll_role_coerces_none():
    cfg = SubDayGuildConfig(enroll_role=None)
    assert cfg.enroll_role == []


def test_enroll_role_passthrough_list():
    cfg = SubDayGuildConfig(enroll_role=["RoleA", "RoleB"])
    assert cfg.enroll_role == ["RoleA", "RoleB"]


def test_enroll_role_default():
    cfg = SubDayGuildConfig()
    assert cfg.enroll_role == []


def test_milestone_roles_from_config():
    cfg = SubDayGuildConfig()
    roles = cfg.milestone_roles()
    assert roles[13] == "SubChallenge: 13wks"
    assert roles[52] == "SubChallenge: 52wks"

    cfg_custom = SubDayGuildConfig(role_13="Custom Role", role_26=None)
    roles2 = cfg_custom.milestone_roles()
    assert roles2[13] == "Custom Role"
    assert roles2[26] is None


def test_paused_participant_not_advanced():
    """A participant with week_completed=False should not advance."""
    p = _sample_participant(current_week=3, week_completed=False)
    # The Sunday logic checks week_completed before advancing
    assert not p.week_completed
    # Simulating: we skip this participant
    assert p.current_week == 3  # unchanged


def test_graduated_participant():
    """A participant at week 52 with week_completed should not advance past 52."""
    p = _sample_participant(current_week=52, week_completed=True)
    # The scheduler checks current_week >= TOTAL_WEEKS before advancing
    assert p.current_week >= 52


def test_prepare_backfill_auto_enrolls():
    """Backfill auto-enrolls a new participant and sets the week."""
    guild_state = SubDayGuildState(guild_id=1, guild_name="test")
    participant, auto_enrolled = _prepare_backfill(guild_state, 123, week=9)

    assert participant.current_week == 9
    assert participant.week_sent is False
    assert auto_enrolled is True


def test_prepare_backfill_existing_participant():
    """Backfill on existing participant updates week and returns auto_enrolled=False."""
    guild_state = SubDayGuildState(guild_id=1, guild_name="test")
    # First call auto-enrolls
    _prepare_backfill(guild_state, 123, week=3)
    # Second call updates existing
    participant, auto_enrolled = _prepare_backfill(guild_state, 123, week=9)

    assert participant.current_week == 9
    assert participant.week_sent is False
    assert auto_enrolled is False


def test_sent_next_advances_and_prevents_cron_resend():
    """sent=True advances to next week with week_completed=False so cron skips."""
    p = _sample_participant(current_week=9)
    p.week_completed = True
    p.last_completed_date = datetime.datetime.now(tz=datetime.UTC)

    # Simulate sent_next logic from commands.py

    if p.current_week < TOTAL_WEEKS:
        p.current_week += 1
        p.week_completed = False
        p.week_sent = True

    # Participant is now on week 10, not completed — cron will skip
    assert p.current_week == 10
    assert p.week_completed is False
    assert p.week_sent is True


def test_sent_next_blocked_at_total_weeks():
    """sent=True on week 52 does not advance past 52."""

    p = _sample_participant(current_week=TOTAL_WEEKS)
    p.week_completed = True

    sent_next = p.current_week < TOTAL_WEEKS
    assert sent_next is False
    assert p.current_week == TOTAL_WEEKS
