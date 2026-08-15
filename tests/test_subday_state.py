import datetime
from unittest.mock import MagicMock

import yaml

from dragonpaw_bot.plugins.subday import state
from dragonpaw_bot.plugins.subday.commands import (
    _owned_sub_status_embed,
    _prepare_backfill,
    _validate_normal_complete,
)
from dragonpaw_bot.plugins.subday.constants import (
    TOTAL_WEEKS,
    next_milestone,
)
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
    assert p.last_completed_date is None


def test_state_yaml_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(state.store, "state_dir", tmp_path)
    state.store.cache.clear()

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
    state.store.cache.clear()
    loaded = state.load(42)
    assert loaded.guild_id == 42
    assert loaded.guild_name == "Test Guild"
    assert 12345 in loaded.participants
    assert loaded.participants[12345].current_week == 1


def test_load_creates_empty_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state.store, "state_dir", tmp_path)
    state.store.cache.clear()

    loaded = state.load(999)
    assert loaded.guild_id == 999
    assert loaded.participants == {}


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


def test_prepare_backfill_auto_enrolls():
    """Backfill auto-enrolls a new participant and sets the week."""
    guild_state = SubDayGuildState(guild_id=1, guild_name="test")
    participant, auto_enrolled = _prepare_backfill(guild_state, 123, week=9)

    assert participant.current_week == 9
    assert auto_enrolled is True


def test_prepare_backfill_existing_participant():
    """Backfill on existing participant updates week and returns auto_enrolled=False."""
    guild_state = SubDayGuildState(guild_id=1, guild_name="test")
    # First call auto-enrolls
    _prepare_backfill(guild_state, 123, week=3)
    # Second call updates existing
    participant, auto_enrolled = _prepare_backfill(guild_state, 123, week=9)

    assert participant.current_week == 9
    assert auto_enrolled is False


# ---------------------------------------------------------------------------- #
#                              next_milestone                                  #
# ---------------------------------------------------------------------------- #


def test_next_milestone_before_first():
    assert next_milestone(1) == 13


def test_next_milestone_on_a_milestone():
    assert next_milestone(26) == 26


def test_next_milestone_between():
    assert next_milestone(27) == 39


def test_next_milestone_past_last():
    assert next_milestone(53) is None


# ---------------------------------------------------------------------------- #
#                               graduated                                      #
# ---------------------------------------------------------------------------- #


def test_graduated_at_final_week_completed():
    p = _sample_participant(current_week=TOTAL_WEEKS, week_completed=True)
    assert p.graduated is True


def test_not_graduated_at_final_week_incomplete():
    p = _sample_participant(current_week=TOTAL_WEEKS, week_completed=False)
    assert p.graduated is False


def test_not_graduated_mid_program():
    p = _sample_participant(current_week=5, week_completed=True)
    assert p.graduated is False


def test_owned_sub_embed_shows_graduated():
    p = _sample_participant(current_week=TOTAL_WEEKS, week_completed=True)
    embed = _owned_sub_status_embed(p, SubDayGuildConfig(), "Subby")
    assert "Graduated" in embed.description


def test_validate_normal_complete_rejects_graduated():
    guild_state = SubDayGuildState(guild_id=1, guild_name="test")
    p = _sample_participant(current_week=TOTAL_WEEKS, week_completed=True)
    guild_state.participants[123] = p
    target = MagicMock()
    target.mention = "@subby"
    error = _validate_normal_complete(guild_state, target, 123)
    assert error is not None and "graduated" in error.lower()
