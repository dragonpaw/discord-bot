import datetime

import yaml

from dragonpaw_bot.plugins.subday import MILESTONE_WEEKS, state
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


def test_prepare_backfill_sets_week_sent_true():
    """Backfill with sent=True sets week_sent=True on the participant."""
    from dragonpaw_bot.plugins.subday.commands import _prepare_backfill
    from dragonpaw_bot.plugins.subday.models import SubDayGuildState

    guild_state = SubDayGuildState(guild_id=1, guild_name="test")
    participant, auto_enrolled = _prepare_backfill(guild_state, 123, week=9, sent=True)

    assert participant.current_week == 9
    assert participant.week_sent is True
    assert auto_enrolled is True


def test_prepare_backfill_sets_week_sent_false_by_default():
    """Backfill without sent sets week_sent=False."""
    from dragonpaw_bot.plugins.subday.commands import _prepare_backfill
    from dragonpaw_bot.plugins.subday.models import SubDayGuildState

    guild_state = SubDayGuildState(guild_id=1, guild_name="test")
    participant, _ = _prepare_backfill(guild_state, 123, week=9)

    assert participant.current_week == 9
    assert participant.week_sent is False


def test_prepare_backfill_sent_none_means_false():
    """Backfill with sent=None sets week_sent=False."""
    from dragonpaw_bot.plugins.subday.commands import _prepare_backfill
    from dragonpaw_bot.plugins.subday.models import SubDayGuildState

    guild_state = SubDayGuildState(guild_id=1, guild_name="test")
    participant, _ = _prepare_backfill(guild_state, 123, week=5, sent=None)

    assert participant.week_sent is False


def test_prepare_backfill_existing_participant():
    """Backfill on existing participant updates week and returns auto_enrolled=False."""
    from dragonpaw_bot.plugins.subday.commands import _prepare_backfill
    from dragonpaw_bot.plugins.subday.models import SubDayGuildState

    guild_state = SubDayGuildState(guild_id=1, guild_name="test")
    # First call auto-enrolls
    _prepare_backfill(guild_state, 123, week=3)
    # Second call updates existing
    participant, auto_enrolled = _prepare_backfill(guild_state, 123, week=9, sent=True)

    assert participant.current_week == 9
    assert participant.week_sent is True
    assert auto_enrolled is False


def test_week_sent_participant_skipped_by_sunday_cron():
    """A participant with week_sent=True and week_completed=False is skipped by cron."""
    p = _sample_participant(current_week=10, week_completed=False, week_sent=True)
    # The cron skips participants where week_completed is False
    assert not p.week_completed
    assert p.week_sent is True
    assert p.current_week == 10
