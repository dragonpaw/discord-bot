"""Security and unit tests for the validation plugin."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

import hikari
import pytest

from dragonpaw_bot.plugins.validation import state as validation_state
from dragonpaw_bot.plugins.validation.commands import _is_staff, _sanitize_channel_name
from dragonpaw_bot.plugins.validation.models import (
    ValidationGuildState,
    ValidationMember,
    ValidationStage,
)

# ---------------------------------------------------------------------------- #
#                          _sanitize_channel_name                               #
# ---------------------------------------------------------------------------- #


def test_sanitize_simple_name():
    assert _sanitize_channel_name("Alice") == "validate-alice"


def test_sanitize_spaces_become_hyphens():
    assert _sanitize_channel_name("John Smith") == "validate-john-smith"


def test_sanitize_strips_special_chars():
    assert _sanitize_channel_name("User#1234") == "validate-user-1234"


def test_sanitize_collapses_multiple_hyphens():
    assert _sanitize_channel_name("Cool  🐉  User") == "validate-cool-user"


def test_sanitize_strips_leading_trailing_hyphens():
    assert _sanitize_channel_name("###Alice###") == "validate-alice"


def test_sanitize_truncated_to_100_chars():
    result = _sanitize_channel_name("a" * 200)
    assert len(result) <= 100
    assert result.startswith("validate-")


def test_sanitize_strips_bracket_suffix():
    # Display names like "Alice [they/them]" should have the tag removed.
    assert _sanitize_channel_name("Alice [they/them]") == "validate-alice"


def test_sanitize_emoji_only_falls_back_to_member():
    # Emoji-only names would produce an empty string — must not yield "validate-".
    result = _sanitize_channel_name("😀🐉🔥")
    assert result == "validate-member"
    assert not result.endswith("-")


def test_sanitize_all_special_chars_falls_back():
    result = _sanitize_channel_name("!!!###$$$")
    assert result == "validate-member"


def test_sanitize_bracket_only_falls_back():
    # "[test]" strips the bracket group leaving an empty name.
    result = _sanitize_channel_name("[test]")
    assert result == "validate-member"


def test_sanitize_mixed_unicode_and_ascii():
    assert _sanitize_channel_name("Ré mi") == "validate-r-mi"


# ---------------------------------------------------------------------------- #
#                                 _is_staff                                     #
# ---------------------------------------------------------------------------- #


def _mock_interaction(
    *,
    has_admin: bool = False,
    role_ids: list[int] | None = None,
) -> Mock:
    member = Mock(spec=hikari.Member)
    perms = hikari.Permissions.ADMINISTRATOR if has_admin else hikari.Permissions.NONE
    member.permissions = perms
    member.role_ids = [hikari.Snowflake(r) for r in (role_ids or [])]
    interaction = Mock(spec=hikari.ComponentInteraction)
    interaction.member = member
    return interaction


def test_is_staff_admin_no_role():
    interaction = _mock_interaction(has_admin=True)
    assert _is_staff(interaction, staff_role_id=None) is True


def test_is_staff_admin_with_role():
    interaction = _mock_interaction(has_admin=True, role_ids=[999])
    assert _is_staff(interaction, staff_role_id=999) is True


def test_is_staff_has_staff_role():
    interaction = _mock_interaction(role_ids=[42])
    assert _is_staff(interaction, staff_role_id=42) is True


def test_is_staff_wrong_role():
    interaction = _mock_interaction(role_ids=[1])
    assert _is_staff(interaction, staff_role_id=42) is False


def test_is_staff_no_role_configured_non_admin():
    interaction = _mock_interaction()
    assert _is_staff(interaction, staff_role_id=None) is False


def test_is_staff_no_member():
    interaction = Mock(spec=hikari.ComponentInteraction)
    interaction.member = None
    assert _is_staff(interaction, staff_role_id=42) is False


# ---------------------------------------------------------------------------- #
#                               Model validation                                #
# ---------------------------------------------------------------------------- #


def test_validation_member_defaults():
    m = ValidationMember(user_id=1, joined_at=datetime.now(UTC))
    assert m.stage == ValidationStage.AWAITING_RULES
    assert m.photo_count == 0
    assert m.reminder_count == 0
    assert m.channel_id is None


def test_validation_member_negative_photo_count_rejected():
    with pytest.raises(Exception):
        ValidationMember(user_id=1, joined_at=datetime.now(UTC), photo_count=-1)


def test_validation_member_negative_reminder_count_rejected():
    with pytest.raises(Exception):
        ValidationMember(user_id=1, joined_at=datetime.now(UTC), reminder_count=-1)


def test_validation_guild_state_defaults():
    st = ValidationGuildState(guild_id=100, guild_name="Test")
    assert st.lobby_channel_id is None
    assert st.member_role_id is None
    assert st.staff_role_id is None
    assert st.max_reminders == 3
    assert st.members == []


def test_validation_guild_state_max_reminders_min_1():
    with pytest.raises(Exception):
        ValidationGuildState(guild_id=1, guild_name="x", max_reminders=0)


def test_validation_guild_state_round_trip():
    now = datetime.now(UTC)
    member = ValidationMember(
        user_id=10,
        joined_at=now,
        stage=ValidationStage.AWAITING_PHOTOS,
        channel_id=500,
        photo_count=1,
    )
    st = ValidationGuildState(
        guild_id=100,
        guild_name="Test Guild",
        lobby_channel_id=200,
        member_role_id=300,
        staff_role_id=400,
        max_reminders=5,
        members=[member],
    )
    data = st.model_dump(mode="json")
    loaded = ValidationGuildState.model_validate(data)
    assert loaded.guild_id == 100
    assert loaded.lobby_channel_id == 200
    assert loaded.member_role_id == 300
    assert loaded.staff_role_id == 400
    assert loaded.max_reminders == 5
    assert len(loaded.members) == 1
    assert loaded.members[0].user_id == 10
    assert loaded.members[0].stage == ValidationStage.AWAITING_PHOTOS
    assert loaded.members[0].channel_id == 500
    assert loaded.members[0].photo_count == 1


# ---------------------------------------------------------------------------- #
#                           State persistence                                   #
# ---------------------------------------------------------------------------- #


def test_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=200,
        guild_name="Test Guild",
        staff_role_id=999,
        members=[
            ValidationMember(
                user_id=1,
                joined_at=now,
                stage=ValidationStage.AWAITING_PHOTOS,
                channel_id=77,
            )
        ],
    )
    validation_state.save(st)
    validation_state._cache.clear()

    loaded = validation_state.load(200)
    assert loaded.guild_id == 200
    assert loaded.staff_role_id == 999
    assert len(loaded.members) == 1
    assert loaded.members[0].stage == ValidationStage.AWAITING_PHOTOS
    assert loaded.members[0].channel_id == 77


def test_state_load_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    loaded = validation_state.load(999)
    assert loaded.guild_id == 999
    assert loaded.members == []


def test_state_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    st = ValidationGuildState(guild_id=300, guild_name="Cached")
    validation_state.save(st)

    first = validation_state.load(300)
    second = validation_state.load(300)
    assert first is second


# ---------------------------------------------------------------------------- #
#                         _close_validate_channel                               #
# ---------------------------------------------------------------------------- #


def _make_gc(rest_mock: Mock) -> Mock:
    bot = Mock()
    bot.rest = rest_mock
    gc = Mock()
    gc.bot = bot
    gc.logger = Mock()
    gc.delete_channel = Mock(return_value=None)
    return gc


async def _noop(*_args, **_kwargs) -> None:
    return None


async def _raise_not_found(*_args, **_kwargs) -> None:
    raise hikari.NotFoundError("", {}, b"")


async def _raise_forbidden(*_args, **_kwargs) -> None:
    raise hikari.ForbiddenError("", {}, b"")


async def _raise_http(*_args, **_kwargs) -> None:
    raise hikari.HTTPError("http error")


async def test_close_validate_channel_happy_path(monkeypatch):
    """Notice is posted, then channel is deleted."""
    from dragonpaw_bot.plugins.validation.commands import _close_validate_channel

    rest = Mock()
    rest.create_message = Mock(return_value=_noop())
    gc = _make_gc(rest)
    gc.delete_channel = Mock(return_value=_noop())

    monkeypatch.setattr("asyncio.sleep", lambda _: _noop())

    await _close_validate_channel(gc, 123, "closing!")

    rest.create_message.assert_called_once_with(channel=123, content="closing!")
    gc.delete_channel.assert_called_once_with(123)


async def test_close_validate_channel_not_found_returns_early(monkeypatch):
    """NotFoundError from create_message short-circuits — no sleep, no delete."""
    from dragonpaw_bot.plugins.validation.commands import _close_validate_channel

    rest = Mock()
    rest.create_message = Mock(return_value=_raise_not_found())
    gc = _make_gc(rest)
    gc.delete_channel = Mock(return_value=_noop())

    sleep_calls = []
    monkeypatch.setattr("asyncio.sleep", lambda d: (sleep_calls.append(d), _noop())[1])

    await _close_validate_channel(gc, 123, "closing!")

    assert sleep_calls == []
    gc.delete_channel.assert_not_called()
    gc.logger.debug.assert_called_once()


async def test_close_validate_channel_forbidden_still_deletes(monkeypatch):
    """ForbiddenError from create_message logs a warning but still deletes the channel."""
    from dragonpaw_bot.plugins.validation.commands import _close_validate_channel

    rest = Mock()
    rest.create_message = Mock(return_value=_raise_forbidden())
    gc = _make_gc(rest)
    gc.delete_channel = Mock(return_value=_noop())

    monkeypatch.setattr("asyncio.sleep", lambda _: _noop())

    await _close_validate_channel(gc, 123, "closing!")

    gc.logger.warning.assert_called_once()
    gc.delete_channel.assert_called_once_with(123)


async def test_close_validate_channel_http_error_still_deletes(monkeypatch):
    """Generic HTTPError from create_message logs a warning but still deletes the channel."""
    from dragonpaw_bot.plugins.validation.commands import _close_validate_channel

    rest = Mock()
    rest.create_message = Mock(return_value=_raise_http())
    gc = _make_gc(rest)
    gc.delete_channel = Mock(return_value=_noop())

    monkeypatch.setattr("asyncio.sleep", lambda _: _noop())

    await _close_validate_channel(gc, 123, "closing!")

    gc.logger.warning.assert_called_once()
    gc.delete_channel.assert_called_once_with(123)
