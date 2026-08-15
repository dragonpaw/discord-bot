"""Security and unit tests for the validation plugin."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import hikari
import pytest

from dragonpaw_bot.plugins.intros import state as intros_state
from dragonpaw_bot.plugins.validation import state as validation_state
from dragonpaw_bot.plugins.validation.commands import (
    APPROVE_BUTTON_PREFIX,
    APPROVE_MODAL_PREFIX,
    RULES_AGREED_PREFIX,
    _channel_ref,
    _close_validate_channel,
    _is_staff,
    _reconcile_guild,
    _sanitize_channel_name,
    handle_approve_button,
    handle_approve_modal,
    handle_rules_agreed,
    on_message_create,
)
from dragonpaw_bot.plugins.validation.cron import validation_reminder_cron
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
    assert st.members == []


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
        members=[member],
    )
    data = st.model_dump(mode="json")
    loaded = ValidationGuildState.model_validate(data)
    assert loaded.guild_id == 100
    assert loaded.lobby_channel_id == 200
    assert loaded.member_role_id == 300
    assert loaded.staff_role_id == 400
    assert len(loaded.members) == 1
    assert loaded.members[0].user_id == 10
    assert loaded.members[0].stage == ValidationStage.AWAITING_PHOTOS
    assert loaded.members[0].channel_id == 500
    assert loaded.members[0].photo_count == 1


# ---------------------------------------------------------------------------- #
#                           State persistence                                   #
# ---------------------------------------------------------------------------- #


def test_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()

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
    validation_state.store.cache.clear()

    loaded = validation_state.load(200)
    assert loaded.guild_id == 200
    assert loaded.staff_role_id == 999
    assert len(loaded.members) == 1
    assert loaded.members[0].stage == ValidationStage.AWAITING_PHOTOS
    assert loaded.members[0].channel_id == 77


def test_state_load_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()

    loaded = validation_state.load(999)
    assert loaded.guild_id == 999
    assert loaded.members == []


def test_state_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()

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

    rest = Mock()
    rest.create_message = AsyncMock()
    gc = _make_gc(rest)
    gc.delete_channel = AsyncMock()

    monkeypatch.setattr("asyncio.sleep", lambda _: _noop())

    await _close_validate_channel(gc, 123, "closing!")

    rest.create_message.assert_called_once_with(channel=123, content="closing!")
    gc.delete_channel.assert_called_once_with(123)


async def test_close_validate_channel_not_found_returns_early(monkeypatch):
    """NotFoundError from create_message short-circuits — no sleep, no delete."""

    rest = Mock()
    rest.create_message = Mock(return_value=_raise_not_found())
    gc = _make_gc(rest)
    gc.delete_channel = AsyncMock()

    sleep_calls = []
    monkeypatch.setattr("asyncio.sleep", lambda d: (sleep_calls.append(d), _noop())[1])

    await _close_validate_channel(gc, 123, "closing!")

    assert sleep_calls == []
    gc.delete_channel.assert_not_called()
    gc.logger.debug.assert_called_once()


async def test_close_validate_channel_forbidden_still_deletes(monkeypatch):
    """ForbiddenError from create_message logs a warning but still deletes the channel."""

    rest = Mock()
    rest.create_message = Mock(return_value=_raise_forbidden())
    gc = _make_gc(rest)
    gc.delete_channel = AsyncMock()

    monkeypatch.setattr("asyncio.sleep", lambda _: _noop())

    await _close_validate_channel(gc, 123, "closing!")

    gc.logger.warning.assert_called_once()
    gc.delete_channel.assert_called_once_with(123)


async def test_close_validate_channel_http_error_still_deletes(monkeypatch):
    """Generic HTTPError from create_message logs a warning but still deletes the channel."""

    rest = Mock()
    rest.create_message = Mock(return_value=_raise_http())
    gc = _make_gc(rest)
    gc.delete_channel = AsyncMock()

    monkeypatch.setattr("asyncio.sleep", lambda _: _noop())

    await _close_validate_channel(gc, 123, "closing!")

    gc.logger.warning.assert_called_once()
    gc.delete_channel.assert_called_once_with(123)


# ---------------------------------------------------------------------------- #
#                              all_guild_ids()                                 #
# ---------------------------------------------------------------------------- #


def test_all_guild_ids_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    assert validation_state.all_guild_ids() == []


def test_all_guild_ids_finds_state_files(tmp_path, monkeypatch):
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    (tmp_path / "validation_111.yaml").touch()
    (tmp_path / "validation_222.yaml").touch()
    (tmp_path / "other_333.yaml").touch()  # not a validation file — must be excluded
    assert sorted(validation_state.all_guild_ids()) == [111, 222]


# ---------------------------------------------------------------------------- #
#                           _reconcile_guild                                    #
# ---------------------------------------------------------------------------- #


def _make_reconcile_bot(*, fetch_member_raises=None, fetch_channel_raises=None):
    """Minimal bot mock for _reconcile_guild tests.

    bot.state() returns None so GuildContext sets log_channel_id=None,
    making gc.log() a silent no-op — no REST create_message calls needed.
    """
    bot = Mock()
    bot.cache = Mock()
    bot.cache.get_guild = Mock(return_value=None)
    bot.state = Mock(return_value=None)

    guild = Mock()
    guild.id = hikari.Snowflake(1)
    guild.name = "Test Guild"

    bot.rest = Mock()
    bot.rest.fetch_guild = AsyncMock(return_value=guild)
    bot.rest.fetch_member = AsyncMock(side_effect=fetch_member_raises)
    bot.rest.fetch_channel = AsyncMock(side_effect=fetch_channel_raises)
    return bot


async def test_reconcile_guild_no_members(tmp_path, monkeypatch):
    """Skip guilds with no members — no REST calls made."""
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()

    st = ValidationGuildState(guild_id=1, guild_name="Test")
    validation_state.save(st)

    bot = _make_reconcile_bot()

    await _reconcile_guild(bot, 1)

    bot.rest.fetch_member.assert_not_called()


async def test_reconcile_guild_member_present_channel_exists(tmp_path, monkeypatch):
    """Member still in guild and channel still exists — no state changes."""
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="Test",
        members=[ValidationMember(user_id=10, joined_at=now, channel_id=99)],
    )
    validation_state.save(st)

    bot = _make_reconcile_bot()

    await _reconcile_guild(bot, 1)

    validation_state.store.cache.clear()
    loaded = validation_state.load(1)
    assert len(loaded.members) == 1


async def test_reconcile_guild_member_left(tmp_path, monkeypatch):
    """Member left while bot was offline — removed from state, channel closed."""
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="Test",
        members=[ValidationMember(user_id=10, joined_at=now, channel_id=99)],
    )
    validation_state.save(st)

    bot = _make_reconcile_bot(fetch_member_raises=hikari.NotFoundError("", {}, b""))
    close_calls: list[int] = []

    async def _fake_close(_gc, channel_id, _notice):
        close_calls.append(channel_id)

    monkeypatch.setattr(
        "dragonpaw_bot.plugins.validation.commands._close_validate_channel",
        _fake_close,
    )

    await _reconcile_guild(bot, 1)
    await asyncio.sleep(0)  # let the create_task coroutine run

    validation_state.store.cache.clear()
    loaded = validation_state.load(1)
    assert loaded.members == []
    assert close_calls == [99]


async def test_reconcile_guild_channel_deleted(tmp_path, monkeypatch):
    """Member present but validate channel was deleted — removed from state."""
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="Test",
        members=[ValidationMember(user_id=10, joined_at=now, channel_id=99)],
    )
    validation_state.save(st)

    bot = _make_reconcile_bot(fetch_channel_raises=hikari.NotFoundError("", {}, b""))

    await _reconcile_guild(bot, 1)

    validation_state.store.cache.clear()
    loaded = validation_state.load(1)
    assert loaded.members == []


async def test_reconcile_guild_no_channel_id_skips_channel_check(tmp_path, monkeypatch):
    """Member still at AWAITING_RULES (no channel yet) and present — no channel fetch."""
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="Test",
        members=[ValidationMember(user_id=10, joined_at=now)],  # channel_id=None
    )
    validation_state.save(st)

    bot = _make_reconcile_bot()

    await _reconcile_guild(bot, 1)

    bot.rest.fetch_channel.assert_not_called()
    validation_state.store.cache.clear()
    loaded = validation_state.load(1)
    assert len(loaded.members) == 1


# ---------------------------------------------------------------------------- #
#                         validation_reminder_cron                              #
# ---------------------------------------------------------------------------- #


def _make_cron_bot(*, guild_id: int = 1, guild_name: str = "TestGuild"):
    """Minimal bot mock for cron tests. bot.state returns None so gc.log() is a no-op."""
    guild = Mock()
    guild.id = hikari.Snowflake(guild_id)
    guild.name = guild_name

    bot = Mock()
    bot.cache = Mock()
    bot.cache.get_guilds_view = Mock(return_value={guild_id: guild})
    bot.state = Mock(return_value=None)
    bot.rest = Mock()
    bot.rest.kick_user = AsyncMock()
    bot.rest.create_message = AsyncMock()
    return bot


# (elapsed_hours, stage, channel_id, expected_channel) — expected_channel None = no reminder.
@pytest.mark.parametrize(
    "case",
    [
        # Below the 16h REMINDER_INTERVAL_HOURS threshold — no reminder.
        (5, ValidationStage.AWAITING_RULES, None, None),
        (15, ValidationStage.AWAITING_RULES, None, None),
        # At/after the threshold — reminder lands in the lobby for AWAITING_RULES
        # and in the validate channel for AWAITING_PHOTOS.
        (18, ValidationStage.AWAITING_RULES, None, 10),
        (40, ValidationStage.AWAITING_RULES, None, 10),
        (18, ValidationStage.AWAITING_PHOTOS, 55, 55),
    ],
)
async def test_cron_reminder_timing(tmp_path, monkeypatch, case):
    """First reminder fires only once REMINDER_INTERVAL_HOURS have elapsed, targeting
    the lobby for AWAITING_RULES and the validate channel for AWAITING_PHOTOS."""
    elapsed_hours, stage, channel_id, expected_channel = case
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="TestGuild",
        lobby_channel_id=10,
        members=[
            ValidationMember(
                user_id=42,
                joined_at=now - timedelta(hours=elapsed_hours),
                stage=stage,
                channel_id=channel_id,
            )
        ],
    )
    validation_state.save(st)

    bot = _make_cron_bot()

    await validation_reminder_cron(bot)

    bot.rest.kick_user.assert_not_called()
    validation_state.store.cache.clear()
    reminder_count = validation_state.load(1).members[0].reminder_count

    if expected_channel is None:
        bot.rest.create_message.assert_not_called()
        assert reminder_count == 0
    else:
        bot.rest.create_message.assert_called_once()
        assert bot.rest.create_message.call_args.kwargs["channel"] == expected_channel
        assert reminder_count == 1


# (stage, channel_id, expect_kick, expected_close_calls)
@pytest.mark.parametrize(
    "case",
    [
        # AWAITING_STAFF is excluded from the deadline — staff handle it manually.
        (ValidationStage.AWAITING_STAFF, 55, False, []),
        # AWAITING_RULES has no validate channel yet — kicked, nothing to close.
        (ValidationStage.AWAITING_RULES, None, True, []),
        # AWAITING_PHOTOS with a channel — kicked and the channel is closed.
        (ValidationStage.AWAITING_PHOTOS, 55, True, [55]),
        # AWAITING_PHOTOS without a channel — still kicked, no close attempted.
        (ValidationStage.AWAITING_PHOTOS, None, True, []),
    ],
)
async def test_cron_deadline(tmp_path, monkeypatch, case):
    """Past the 4-day deadline, members are kicked and dropped from state (closing their
    validate channel if any) — except AWAITING_STAFF, which is left for manual review."""
    stage, channel_id, expect_kick, expected_close_calls = case
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="TestGuild",
        lobby_channel_id=10,
        members=[
            ValidationMember(
                user_id=42,
                joined_at=now - timedelta(days=5),  # just past the 4-day deadline
                stage=stage,
                channel_id=channel_id,
            )
        ],
    )
    validation_state.save(st)

    bot = _make_cron_bot()
    close_calls: list[int] = []

    async def _fake_close(_gc, channel_id, _notice):
        close_calls.append(channel_id)

    monkeypatch.setattr(
        "dragonpaw_bot.plugins.validation.cron._close_validate_channel",
        _fake_close,
    )

    await validation_reminder_cron(bot)
    await asyncio.sleep(0)  # the close runs as a background task now

    assert close_calls == expected_close_calls
    validation_state.store.cache.clear()
    remaining = validation_state.load(1).members

    if expect_kick:
        bot.rest.kick_user.assert_called_once()
        assert remaining == []
    else:
        bot.rest.kick_user.assert_not_called()
        bot.rest.create_message.assert_not_called()
        assert len(remaining) == 1


async def test_cron_deadline_removes_state_before_kick(tmp_path, monkeypatch):
    """The member is dropped from state and saved *before* the kick REST call, so the
    resulting MemberDeleteEvent finds no entry and on_member_leave stays silent (no
    false "flew away" log, no redundant channel close)."""
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="TestGuild",
        lobby_channel_id=10,
        members=[
            ValidationMember(
                user_id=42,
                joined_at=now - timedelta(days=5),
                stage=ValidationStage.AWAITING_RULES,
            )
        ],
    )
    validation_state.save(st)

    bot = _make_cron_bot()
    members_at_kick: list[list[int]] = []

    async def _capture(*_args, **_kwargs):
        members_at_kick.append([m.user_id for m in validation_state.load(1).members])

    bot.rest.kick_user.side_effect = _capture

    await validation_reminder_cron(bot)

    assert members_at_kick == [[]]  # state already emptied by the time the kick fired


async def test_cron_deadline_close_failure_does_not_stop_other_members(
    tmp_path, monkeypatch
):
    """A failing channel close must not abort the guild's sweep: every past-deadline
    member still gets kicked and removed from state."""
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="TestGuild",
        lobby_channel_id=10,
        members=[
            ValidationMember(
                user_id=42,
                joined_at=now - timedelta(days=5),
                stage=ValidationStage.AWAITING_PHOTOS,
                channel_id=55,
            ),
            ValidationMember(
                user_id=43,
                joined_at=now - timedelta(days=5),
                stage=ValidationStage.AWAITING_PHOTOS,
                channel_id=56,
            ),
        ],
    )
    validation_state.save(st)

    bot = _make_cron_bot()

    async def _failing_close(_gc, _channel_id, _notice):
        raise RuntimeError("close blew up")

    monkeypatch.setattr(
        "dragonpaw_bot.plugins.validation.cron._close_validate_channel",
        _failing_close,
    )

    await validation_reminder_cron(bot)
    await asyncio.sleep(0)  # let the scheduled close tasks run (and fail)

    assert bot.rest.kick_user.call_count == 2
    validation_state.store.cache.clear()
    assert validation_state.load(1).members == []


async def test_cron_deadline_does_not_block_on_channel_close(tmp_path, monkeypatch):
    """The close helper sleeps 30s before deleting; the cron must schedule it in the
    background, not await it inline."""
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="TestGuild",
        lobby_channel_id=10,
        members=[
            ValidationMember(
                user_id=42,
                joined_at=now - timedelta(days=5),
                stage=ValidationStage.AWAITING_PHOTOS,
                channel_id=55,
            ),
        ],
    )
    validation_state.save(st)

    bot = _make_cron_bot()
    blocker = asyncio.Event()
    close_started = asyncio.Event()

    async def _hanging_close(_gc, _channel_id, _notice):
        close_started.set()
        await blocker.wait()

    monkeypatch.setattr(
        "dragonpaw_bot.plugins.validation.cron._close_validate_channel",
        _hanging_close,
    )

    await asyncio.wait_for(validation_reminder_cron(bot), timeout=1)

    await asyncio.wait_for(close_started.wait(), timeout=1)
    blocker.set()  # unblock so the background task can finish cleanly
    await asyncio.sleep(0)
    bot.rest.kick_user.assert_called_once()


def test_channel_ref_configured():
    assert _channel_ref(555, "#fallback") == "<#555>"


def test_channel_ref_fallback():
    assert _channel_ref(None, "#fallback") == "#fallback"


# ---------------------------------------------------------------------------- #
#                      Interaction handler test scaffolding                     #
# ---------------------------------------------------------------------------- #


GUILD_ID = hikari.Snowflake(1)


def _use_tmp_state(tmp_path, monkeypatch) -> None:
    """Point both the validation and intros stores at tmp_path with empty caches.

    The intros store matters because handle_approve_modal reads it to decide
    whether to pin the missing-intro role — without this it would read the real
    state/ directory.
    """
    monkeypatch.setattr(validation_state.store, "state_dir", tmp_path)
    validation_state.store.cache.clear()
    monkeypatch.setattr(intros_state.store, "state_dir", tmp_path)
    intros_state.store.cache.clear()


def _make_handler_bot(*, guild_id: int = 1, guild_name: str = "TestGuild"):
    """Bot mock for interaction/listener tests.

    bot.state() returns None so GuildContext has no log channel and gc.log() is a
    silent no-op (and no general-channel announcement is attempted).
    """
    guild = Mock()
    guild.id = hikari.Snowflake(guild_id)
    guild.name = guild_name

    bot = Mock()
    bot.user_id = hikari.Snowflake(999)
    bot.cache = Mock()
    bot.cache.get_guild = Mock(return_value=guild)
    bot.state = Mock(return_value=None)

    bot.rest = Mock()
    bot.rest.create_message = AsyncMock()
    bot.rest.edit_member = AsyncMock()
    bot.rest.add_role_to_member = AsyncMock()
    bot.rest.delete_channel = AsyncMock()

    channel = Mock()
    channel.id = hikari.Snowflake(77)
    bot.rest.create_guild_text_channel = AsyncMock(return_value=channel)

    row = Mock()
    row.add_interactive_button = Mock()
    bot.rest.build_message_action_row = Mock(return_value=row)
    return bot


def _make_member(
    user_id: int,
    *,
    has_admin: bool = False,
    role_ids: list[int] | None = None,
    display_name: str = "Clicker",
):
    member = Mock(spec=hikari.InteractionMember)
    member.id = hikari.Snowflake(user_id)
    member.display_name = display_name
    member.permissions = (
        hikari.Permissions.ADMINISTRATOR if has_admin else hikari.Permissions.NONE
    )
    member.role_ids = [hikari.Snowflake(r) for r in (role_ids or [])]
    return member


def _make_component_interaction(bot, member, custom_id: str):
    user = Mock(spec=hikari.User)
    user.id = member.id
    user.mention = f"<@{member.id}>"

    interaction = Mock(spec=hikari.ComponentInteraction)
    interaction.app = bot
    interaction.guild_id = GUILD_ID
    interaction.custom_id = custom_id
    interaction.member = member
    interaction.user = user
    interaction.created_at = datetime.now(UTC)
    interaction.create_initial_response = AsyncMock()
    interaction.edit_initial_response = AsyncMock()
    interaction.create_modal_response = AsyncMock()
    return interaction


def _make_modal_interaction(bot, member, custom_id: str, name: str = "Approved Name"):
    user = Mock(spec=hikari.User)
    user.id = member.id

    component = Mock()
    component.custom_id = "validation_name_input"
    component.value = name
    row = Mock()
    row.components = [component]

    interaction = Mock(spec=hikari.ModalInteraction)
    interaction.app = bot
    interaction.guild_id = GUILD_ID
    interaction.custom_id = custom_id
    interaction.member = member
    interaction.user = user
    interaction.components = [row]
    interaction.create_initial_response = AsyncMock()
    interaction.edit_initial_response = AsyncMock()
    return interaction


def _response_text(mock_call) -> str:
    """The content kwarg of a create_initial_response / edit_initial_response call."""
    return mock_call.kwargs["content"]


def _saved_members(guild_id: int = 1):
    """Re-read the guild's members from disk, bypassing the in-memory cache."""
    validation_state.store.cache.clear()
    return validation_state.load(guild_id).members


# ---------------------------------------------------------------------------- #
#                    handle_rules_agreed — button ownership                     #
# ---------------------------------------------------------------------------- #


async def test_rules_agreed_wrong_clicker_rejected(tmp_path, monkeypatch):
    """The button embeds the target's user ID; anyone else clicking it is refused and
    nothing about the target's onboarding changes.

    Fails if the `int(interaction.user.id) != expected_user_id` guard is dropped or
    inverted — the impostor would then get a validate channel opened for the target.
    """
    _use_tmp_state(tmp_path, monkeypatch)

    now = datetime.now(UTC)
    validation_state.save(
        ValidationGuildState(
            guild_id=1,
            guild_name="TestGuild",
            staff_role_id=400,
            members=[ValidationMember(user_id=42, joined_at=now)],
        )
    )

    bot = _make_handler_bot()
    interaction = _make_component_interaction(
        bot, _make_member(99), f"{RULES_AGREED_PREFIX}42"
    )

    await handle_rules_agreed(interaction)

    interaction.create_initial_response.assert_called_once()
    call = interaction.create_initial_response.call_args
    assert call.kwargs["response_type"] == hikari.ResponseType.MESSAGE_CREATE
    assert call.kwargs["flags"] == hikari.MessageFlag.EPHEMERAL
    assert "isn't for you" in _response_text(call)

    bot.rest.create_guild_text_channel.assert_not_called()
    interaction.edit_initial_response.assert_not_called()

    members = _saved_members()
    assert len(members) == 1
    assert members[0].user_id == 42
    assert members[0].stage == ValidationStage.AWAITING_RULES
    assert members[0].channel_id is None


async def test_rules_agreed_tagged_member_gets_channel(tmp_path, monkeypatch):
    """The tagged member clicking their own button gets a private validate channel and
    advances to AWAITING_PHOTOS, persisted to disk.

    Fails if the ownership guard rejects the rightful clicker, or if the stage /
    channel_id update is not saved (a restart would strand them at AWAITING_RULES).
    """
    _use_tmp_state(tmp_path, monkeypatch)

    now = datetime.now(UTC)
    validation_state.save(
        ValidationGuildState(
            guild_id=1,
            guild_name="TestGuild",
            staff_role_id=400,
            validate_category_id=7,
            members=[ValidationMember(user_id=42, joined_at=now)],
        )
    )

    bot = _make_handler_bot()
    interaction = _make_component_interaction(
        bot, _make_member(42, display_name="Newbie"), f"{RULES_AGREED_PREFIX}42"
    )

    await handle_rules_agreed(interaction)

    deferred = interaction.create_initial_response.call_args
    assert (
        deferred.kwargs["response_type"] == hikari.ResponseType.DEFERRED_MESSAGE_CREATE
    )

    bot.rest.create_guild_text_channel.assert_called_once()
    create_kwargs = bot.rest.create_guild_text_channel.call_args.kwargs
    assert create_kwargs["name"] == "validate-newbie"
    assert create_kwargs["category"] == hikari.Snowflake(7)

    members = _saved_members()
    assert len(members) == 1
    assert members[0].stage == ValidationStage.AWAITING_PHOTOS
    assert members[0].channel_id == 77

    assert "<#77>" in _response_text(interaction.edit_initial_response.call_args)


async def test_rules_agreed_unknown_member_creates_no_channel(tmp_path, monkeypatch):
    """A clicker who matches the custom ID but has no state entry (e.g. state was
    cleaned up) is told to ask staff — no channel is created.

    Fails if the `member_entry` lookup stops short-circuiting, which would create an
    orphan channel with nothing tracking it.
    """
    _use_tmp_state(tmp_path, monkeypatch)

    validation_state.save(ValidationGuildState(guild_id=1, guild_name="TestGuild"))

    bot = _make_handler_bot()
    interaction = _make_component_interaction(
        bot, _make_member(42), f"{RULES_AGREED_PREFIX}42"
    )

    await handle_rules_agreed(interaction)

    bot.rest.create_guild_text_channel.assert_not_called()
    assert "onboarding list" in _response_text(
        interaction.edit_initial_response.call_args
    )


async def test_rules_agreed_second_click_reuses_channel(tmp_path, monkeypatch):
    """Clicking again once past AWAITING_RULES points at the existing channel instead
    of opening a second one.

    Fails if the stage check is removed — every extra click would spawn another
    validate channel.
    """
    _use_tmp_state(tmp_path, monkeypatch)

    now = datetime.now(UTC)
    validation_state.save(
        ValidationGuildState(
            guild_id=1,
            guild_name="TestGuild",
            members=[
                ValidationMember(
                    user_id=42,
                    joined_at=now,
                    stage=ValidationStage.AWAITING_PHOTOS,
                    channel_id=55,
                )
            ],
        )
    )

    bot = _make_handler_bot()
    interaction = _make_component_interaction(
        bot, _make_member(42), f"{RULES_AGREED_PREFIX}42"
    )

    await handle_rules_agreed(interaction)

    bot.rest.create_guild_text_channel.assert_not_called()
    assert "<#55>" in _response_text(interaction.edit_initial_response.call_args)
    assert _saved_members()[0].channel_id == 55


# ---------------------------------------------------------------------------- #
#                  handle_approve_button — self-approval / staff                #
# ---------------------------------------------------------------------------- #


def _approve_state(**overrides) -> ValidationGuildState:
    """State with member 42 awaiting staff review in channel 99, staff role 400."""
    fields = {
        "guild_id": 1,
        "guild_name": "TestGuild",
        "staff_role_id": 400,
        "member_role_id": 300,
        "members": [
            ValidationMember(
                user_id=42,
                joined_at=datetime.now(UTC),
                stage=ValidationStage.AWAITING_STAFF,
                channel_id=99,
                photo_count=2,
            )
        ],
    }
    fields.update(overrides)
    return ValidationGuildState(**fields)


async def test_approve_button_self_approval_rejected(tmp_path, monkeypatch):
    """The member under review can't approve themselves — even holding ADMINISTRATOR,
    which would otherwise satisfy the staff check.

    Fails if the self-approval check is removed, or moved after the _is_staff check
    (an admin verifying themselves would then be handed the modal).
    """
    _use_tmp_state(tmp_path, monkeypatch)
    validation_state.save(_approve_state())

    bot = _make_handler_bot()
    interaction = _make_component_interaction(
        bot,
        _make_member(42, has_admin=True, role_ids=[400]),
        f"{APPROVE_BUTTON_PREFIX}99",
    )

    await handle_approve_button(interaction)

    interaction.create_modal_response.assert_not_called()
    call = interaction.create_initial_response.call_args
    assert call.kwargs["flags"] == hikari.MessageFlag.EPHEMERAL
    assert "can't approve your own" in _response_text(call)


async def test_approve_button_non_staff_rejected(tmp_path, monkeypatch):
    """A random member in the validate channel gets an ephemeral refusal and never
    sees the name modal.

    Fails if the _is_staff gate is dropped from handle_approve_button — any member
    who can see the channel could then submit an approval.
    """
    _use_tmp_state(tmp_path, monkeypatch)
    validation_state.save(_approve_state())

    bot = _make_handler_bot()
    interaction = _make_component_interaction(
        bot, _make_member(77, role_ids=[123]), f"{APPROVE_BUTTON_PREFIX}99"
    )

    await handle_approve_button(interaction)

    interaction.create_modal_response.assert_not_called()
    call = interaction.create_initial_response.call_args
    assert call.kwargs["flags"] == hikari.MessageFlag.EPHEMERAL
    assert "Only staff" in _response_text(call)


@pytest.mark.parametrize(
    ("has_admin", "role_ids"),
    [(False, [400]), (True, [])],  # staff role holder, and a plain administrator
)
async def test_approve_button_staff_gets_modal(
    tmp_path, monkeypatch, has_admin, role_ids
):
    """Staff (by role or by ADMINISTRATOR) get the name-entry modal, keyed to the same
    channel ID as the button.

    Fails if the modal custom ID stops carrying the channel ID — handle_approve_modal
    would no longer find the state entry to approve.
    """
    _use_tmp_state(tmp_path, monkeypatch)
    validation_state.save(_approve_state())

    bot = _make_handler_bot()
    interaction = _make_component_interaction(
        bot,
        _make_member(77, has_admin=has_admin, role_ids=role_ids),
        f"{APPROVE_BUTTON_PREFIX}99",
    )

    await handle_approve_button(interaction)

    interaction.create_initial_response.assert_not_called()
    interaction.create_modal_response.assert_called_once()
    assert (
        interaction.create_modal_response.call_args.kwargs["custom_id"]
        == f"{APPROVE_MODAL_PREFIX}99"
    )


# ---------------------------------------------------------------------------- #
#                          handle_approve_modal                                 #
# ---------------------------------------------------------------------------- #


async def test_approve_modal_self_approval_rejected(tmp_path, monkeypatch):
    """Self-approval is refused at the modal too — the button check isn't the only
    gate, since a modal can be submitted for a custom ID obtained earlier.

    Fails if the self-approval check is removed from handle_approve_modal: the member
    would nickname themselves and take the member role.
    """
    _use_tmp_state(tmp_path, monkeypatch)
    validation_state.save(_approve_state())

    bot = _make_handler_bot()
    interaction = _make_modal_interaction(
        bot, _make_member(42, has_admin=True), f"{APPROVE_MODAL_PREFIX}99"
    )

    await handle_approve_modal(interaction)

    assert "can't approve your own" in _response_text(
        interaction.edit_initial_response.call_args
    )
    bot.rest.edit_member.assert_not_called()
    bot.rest.add_role_to_member.assert_not_called()
    assert [m.user_id for m in _saved_members()] == [42]


async def test_approve_modal_non_staff_rejected(tmp_path, monkeypatch):
    """A non-staff modal submission is refused before any approval work.

    Fails if the _is_staff gate is dropped from handle_approve_modal.
    """
    _use_tmp_state(tmp_path, monkeypatch)
    validation_state.save(_approve_state())

    bot = _make_handler_bot()
    interaction = _make_modal_interaction(
        bot, _make_member(77, role_ids=[123]), f"{APPROVE_MODAL_PREFIX}99"
    )

    await handle_approve_modal(interaction)

    assert "Only staff" in _response_text(interaction.edit_initial_response.call_args)
    bot.rest.edit_member.assert_not_called()
    assert [m.user_id for m in _saved_members()] == [42]


@pytest.mark.parametrize("name", ["   ", "\t\n ", ""])
async def test_approve_modal_blank_name_rejected(tmp_path, monkeypatch, name):
    """A whitespace-only name is empty after .strip() and must not be accepted.

    Fails if the .strip() is dropped from the name read — "   " is truthy, so the
    member would be approved with a whitespace nickname.
    """
    _use_tmp_state(tmp_path, monkeypatch)
    validation_state.save(_approve_state())

    bot = _make_handler_bot()
    interaction = _make_modal_interaction(
        bot, _make_member(77, role_ids=[400]), f"{APPROVE_MODAL_PREFIX}99", name=name
    )

    await handle_approve_modal(interaction)

    assert "didn't catch a name" in _response_text(
        interaction.edit_initial_response.call_args
    )
    bot.rest.edit_member.assert_not_called()
    bot.rest.add_role_to_member.assert_not_called()
    assert [m.user_id for m in _saved_members()] == [42]


async def test_approve_modal_happy_path_saves_before_rest_work(tmp_path, monkeypatch):
    """Approval drops the member from state and saves *before* the nickname/role REST
    calls, so a failure part-way through can't leave a re-approvable entry behind.

    Fails if the state removal + save move below the edit_member/add_role_to_member
    block (the captured on-disk state would still list the member).
    """
    _use_tmp_state(tmp_path, monkeypatch)
    validation_state.save(_approve_state())

    bot = _make_handler_bot()
    seen_at_rest: list[list[int]] = []

    async def _capture_members(*_args, **_kwargs):
        seen_at_rest.append([m.user_id for m in _saved_members()])

    bot.rest.edit_member.side_effect = _capture_members
    bot.rest.add_role_to_member.side_effect = _capture_members

    close_calls: list[int] = []

    async def _fake_close(_gc, channel_id, _notice):
        close_calls.append(channel_id)

    monkeypatch.setattr(
        "dragonpaw_bot.plugins.validation.commands._close_validate_channel",
        _fake_close,
    )

    interaction = _make_modal_interaction(
        bot,
        _make_member(77, role_ids=[400]),
        f"{APPROVE_MODAL_PREFIX}99",
        name="  Sparky  ",
    )

    await handle_approve_modal(interaction)
    await asyncio.sleep(0)  # let the scheduled channel close run

    assert seen_at_rest == [[], []]  # state already empty at both REST calls
    assert _saved_members() == []

    bot.rest.edit_member.assert_called_once()
    assert bot.rest.edit_member.call_args.kwargs["nickname"] == "Sparky"
    bot.rest.add_role_to_member.assert_called_once_with(
        interaction.guild_id, hikari.Snowflake(42), hikari.Snowflake(300)
    )
    assert close_calls == [99]
    assert "Sparky" in _response_text(interaction.edit_initial_response.call_args)


async def test_approve_modal_unknown_channel_does_nothing(tmp_path, monkeypatch):
    """A modal for a channel with no state entry (already approved elsewhere) does no
    REST work and leaves other members' entries alone.

    Fails if the `if not member_entry` guard is removed — approval would run against
    a None entry.
    """
    _use_tmp_state(tmp_path, monkeypatch)
    validation_state.save(_approve_state())

    bot = _make_handler_bot()
    interaction = _make_modal_interaction(
        bot, _make_member(77, role_ids=[400]), f"{APPROVE_MODAL_PREFIX}12345"
    )

    await handle_approve_modal(interaction)

    assert "couldn't find that validation entry" in _response_text(
        interaction.edit_initial_response.call_args
    )
    bot.rest.edit_member.assert_not_called()
    assert [m.user_id for m in _saved_members()] == [42]


# ---------------------------------------------------------------------------- #
#                  on_message_create — photo counting isolation                 #
# ---------------------------------------------------------------------------- #


def _make_message_event(
    bot,
    *,
    channel_id: int,
    author_id: int,
    media_types: tuple[str | None, ...] = ("image/png",),
    is_bot: bool = False,
):
    attachments = []
    for media_type in media_types:
        attachment = Mock(spec=hikari.Attachment)
        attachment.media_type = media_type
        attachments.append(attachment)

    message = Mock(spec=hikari.Message)
    message.attachments = attachments

    member = Mock(spec=hikari.Member)
    member.display_name = "Newbie"

    event = Mock(spec=hikari.GuildMessageCreateEvent)
    event.app = bot
    event.is_bot = is_bot
    event.guild_id = GUILD_ID
    event.channel_id = hikari.Snowflake(channel_id)
    event.author_id = hikari.Snowflake(author_id)
    event.message = message
    event.member = member
    return event


def _photos_state(stage: ValidationStage = ValidationStage.AWAITING_PHOTOS, **kwargs):
    """Member 42 in validate channel 55, staff role 400."""
    return ValidationGuildState(
        guild_id=1,
        guild_name="TestGuild",
        staff_role_id=400,
        members=[
            ValidationMember(
                user_id=42,
                joined_at=datetime.now(UTC),
                stage=stage,
                channel_id=55,
                **kwargs,
            )
        ],
    )


# (label, channel_id, author_id, stage, is_bot, media_types)
@pytest.mark.parametrize(
    "case",
    [
        (
            "other channel",
            56,
            42,
            ValidationStage.AWAITING_PHOTOS,
            False,
            ("image/png",),
        ),
        (
            "other author",
            55,
            43,
            ValidationStage.AWAITING_PHOTOS,
            False,
            ("image/png",),
        ),
        ("wrong stage", 55, 42, ValidationStage.AWAITING_STAFF, False, ("image/png",)),
        ("bot message", 55, 42, ValidationStage.AWAITING_PHOTOS, True, ("image/png",)),
        (
            "non-image",
            55,
            42,
            ValidationStage.AWAITING_PHOTOS,
            False,
            ("video/mp4", None),
        ),
    ],
)
async def test_photo_counting_ignores_unrelated_messages(tmp_path, monkeypatch, case):
    """Photos only count in the member's own validate channel, from that member, while
    they're at AWAITING_PHOTOS — and only actual images count.

    Fails if any of the three match conditions is dropped from the member lookup (a
    bystander posting two images elsewhere would advance someone else's onboarding),
    or if the image/ media-type filter goes away.
    """
    _label, channel_id, author_id, stage, is_bot, media_types = case
    _use_tmp_state(tmp_path, monkeypatch)
    validation_state.save(_photos_state(stage))

    bot = _make_handler_bot()
    event = _make_message_event(
        bot,
        channel_id=channel_id,
        author_id=author_id,
        media_types=media_types,
        is_bot=is_bot,
    )

    await on_message_create(event)

    bot.rest.create_message.assert_not_called()
    member = _saved_members()[0]
    assert member.photo_count == 0
    assert member.stage == stage


async def test_photo_counting_single_photo_does_not_advance(tmp_path, monkeypatch):
    """One image is counted and persisted but the member stays at AWAITING_PHOTOS —
    no staff ping until MIN_PHOTOS is reached.

    Fails if the >= MIN_PHOTOS threshold drops to 1, pinging staff on a half-finished
    submission.
    """
    _use_tmp_state(tmp_path, monkeypatch)
    validation_state.save(_photos_state())

    bot = _make_handler_bot()
    event = _make_message_event(bot, channel_id=55, author_id=42)

    await on_message_create(event)

    bot.rest.create_message.assert_not_called()
    member = _saved_members()[0]
    assert member.photo_count == 1
    assert member.stage == ValidationStage.AWAITING_PHOTOS


async def test_photo_counting_two_photos_pings_staff(tmp_path, monkeypatch):
    """Two images in one message advance the member to AWAITING_STAFF (persisted) and
    post the staff ping with an approve button keyed to that channel.

    Fails if the stage transition isn't saved, or the approve button's custom ID stops
    carrying the validate channel ID.
    """
    _use_tmp_state(tmp_path, monkeypatch)
    validation_state.save(_photos_state())

    bot = _make_handler_bot()
    event = _make_message_event(
        bot, channel_id=55, author_id=42, media_types=("image/png", "image/jpeg")
    )

    await on_message_create(event)

    member = _saved_members()[0]
    assert member.photo_count == 2
    assert member.stage == ValidationStage.AWAITING_STAFF

    bot.rest.create_message.assert_called_once()
    ping_kwargs = bot.rest.create_message.call_args.kwargs
    assert ping_kwargs["channel"] == event.channel_id
    assert "<@&400>" in ping_kwargs["content"]

    button_args = bot.rest.build_message_action_row.return_value.add_interactive_button
    button_args.assert_called_once()
    assert button_args.call_args.args[1] == f"{APPROVE_BUTTON_PREFIX}55"


async def test_photo_counting_accumulates_across_messages(tmp_path, monkeypatch):
    """Two separate one-image messages reach MIN_PHOTOS just like a single two-image
    message — the count is cumulative and persisted between events.

    Fails if photo_count is reassigned rather than incremented, or if the sub-threshold
    branch stops saving (the first photo would be lost).
    """
    _use_tmp_state(tmp_path, monkeypatch)
    validation_state.save(_photos_state())

    bot = _make_handler_bot()

    await on_message_create(_make_message_event(bot, channel_id=55, author_id=42))
    assert _saved_members()[0].photo_count == 1
    bot.rest.create_message.assert_not_called()

    await on_message_create(_make_message_event(bot, channel_id=55, author_id=42))

    member = _saved_members()[0]
    assert member.photo_count == 2
    assert member.stage == ValidationStage.AWAITING_STAFF
    bot.rest.create_message.assert_called_once()
