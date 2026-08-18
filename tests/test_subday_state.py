import datetime
import unittest.mock
from unittest.mock import AsyncMock, MagicMock, Mock

import hikari
import yaml

from dragonpaw_bot.colors import SOLARIZED_VIOLET
from dragonpaw_bot.plugins.subday import state
from dragonpaw_bot.plugins.subday.commands import (
    _do_signup_async,
    _owned_sub_status_embed,
    _prepare_backfill,
    _progress_footer,
    _role_mention,
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


def test_owned_sub_embed_graduated_has_no_prize_teaser():
    """A graduated sub has earned every prize — no upcoming-prize teaser."""
    p = _sample_participant(current_week=TOTAL_WEEKS, week_completed=True)
    embed = _owned_sub_status_embed(p, SubDayGuildConfig(), "Subby")
    assert "🎁" not in embed.description


def test_validate_normal_complete_rejects_graduated():
    guild_state = SubDayGuildState(guild_id=1, guild_name="test")
    p = _sample_participant(current_week=TOTAL_WEEKS, week_completed=True)
    guild_state.participants[123] = p
    target = MagicMock()
    target.mention = "@subby"
    error = _validate_normal_complete(guild_state, target, 123)
    assert error is not None and "graduated" in error.lower()


# ---------------------------------------------------------------------------- #
#                          signup praise post                                  #
# ---------------------------------------------------------------------------- #


def _signup_fixtures(tmp_path, monkeypatch, achievements_channel):
    monkeypatch.setattr(state.store, "state_dir", tmp_path)
    state.store.cache.clear()
    gs = SubDayGuildState(guild_id=1, guild_name="G")
    gs.config.achievements_channel = achievements_channel
    gs.participants[42] = _sample_participant(user_id=42)
    state.save(gs)

    user = MagicMock()
    user.id = 42
    user.display_name = "Newbie"
    user.username = "newbie"
    user.mention = "<@42>"
    user.fetch_dm_channel = AsyncMock()

    bot = MagicMock()
    guild = MagicMock()
    guild.id = hikari.Snowflake(1)
    guild.name = "G"
    bot.rest.fetch_guild = AsyncMock(return_value=guild)
    bot.state = Mock(return_value=None)  # no log channel

    channel = MagicMock()
    channel.send = AsyncMock()
    lookup = AsyncMock(return_value=channel)
    monkeypatch.setattr("dragonpaw_bot.utils.guild_channel_by_name", lookup)
    return bot, user, channel, lookup


async def test_signup_praise_posted_to_achievements_channel(tmp_path, monkeypatch):
    bot, user, channel, _lookup = _signup_fixtures(tmp_path, monkeypatch, "wins")

    await _do_signup_async(bot, hikari.Snowflake(1), user)

    channel.send.assert_awaited_once()
    args = channel.send.call_args
    assert args.args[0] == "<@42>"  # ping lives in content; embed mentions don't notify
    embed = args.kwargs["embed"]
    assert embed.color == SOLARIZED_VIOLET
    assert "<@42>" in embed.description
    assert "Where I am Led" in embed.description


async def test_signup_praise_skipped_when_channel_unconfigured(tmp_path, monkeypatch):
    bot, user, channel, lookup = _signup_fixtures(tmp_path, monkeypatch, None)

    await _do_signup_async(bot, hikari.Snowflake(1), user)

    lookup.assert_not_awaited()
    channel.send.assert_not_awaited()


def test_progress_footer_shows_week_and_signup():
    p = _sample_participant(current_week=7)
    footer = _progress_footer(p)
    assert f"7/{TOTAL_WEEKS} weeks" in footer
    assert "**Signed up**:" in footer


async def test_role_mention_prefers_real_role():
    gc = MagicMock()
    role = MagicMock()
    role.mention = "<@&5>"
    with_role = AsyncMock(return_value=role)
    with unittest.mock.patch("dragonpaw_bot.utils.guild_role_by_name", with_role):
        assert await _role_mention(gc, "Staff") == "<@&5>"


async def test_role_mention_falls_back_to_bold_name():
    gc = MagicMock()
    with unittest.mock.patch(
        "dragonpaw_bot.utils.guild_role_by_name", AsyncMock(return_value=None)
    ):
        assert await _role_mention(gc, "Staff") == "**Staff**"


async def test_role_mention_none_role():
    assert await _role_mention(MagicMock(), None) is None
