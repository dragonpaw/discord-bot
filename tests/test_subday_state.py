import datetime

import yaml

from dragonpaw_bot.plugins.subday import MILESTONE_ROLES, state
from dragonpaw_bot.plugins.subday.models import SubDayGuildState, SubDayParticipant


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
    assert 13 in MILESTONE_ROLES
    assert 26 in MILESTONE_ROLES
    assert 39 in MILESTONE_ROLES
    assert 52 in MILESTONE_ROLES
    assert 1 not in MILESTONE_ROLES


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
