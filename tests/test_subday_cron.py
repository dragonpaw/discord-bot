import datetime
from unittest.mock import AsyncMock, MagicMock

import hikari
import pytest

from dragonpaw_bot.plugins.subday import commands, cron, state
from dragonpaw_bot.plugins.subday.constants import (
    SUBDAY_OWNER_REQUEST_PREFIX,
    TOTAL_WEEKS,
)
from dragonpaw_bot.plugins.subday.models import SubDayGuildState, SubDayParticipant

GUILD_ID = 4242


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state.store, "state_dir", tmp_path)
    state.store.cache.clear()
    yield
    state.store.cache.clear()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The cron paces itself with asyncio.sleep(1) between DMs."""
    monkeypatch.setattr(cron.asyncio, "sleep", AsyncMock())


def _participant(**kwargs) -> SubDayParticipant:
    defaults = {
        "user_id": 12345,
        "signup_date": datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC),
    }
    defaults.update(kwargs)
    return SubDayParticipant(**defaults)


def _guild() -> MagicMock:
    guild = MagicMock()
    guild.id = GUILD_ID
    guild.name = "Test Guild"
    return guild


def _member_with_dm() -> tuple[MagicMock, AsyncMock]:
    dm = MagicMock()
    dm.send = AsyncMock()
    member = MagicMock()
    member.user.fetch_dm_channel = AsyncMock(return_value=dm)
    return member, dm.send


# ---------------------------------------------------------------------------- #
#                            _advance_participant                              #
# ---------------------------------------------------------------------------- #


async def test_advance_skips_incomplete_week(monkeypatch):
    member, send = _member_with_dm()
    monkeypatch.setattr(cron, "guild_member", AsyncMock(return_value=member))
    participant = _participant(current_week=5, week_completed=False)

    result = await cron._advance_participant(
        MagicMock(), _guild(), 12345, participant, {}
    )

    assert result is False
    assert participant.current_week == 5
    send.assert_not_awaited()


async def test_advance_skips_graduated_participant(monkeypatch):
    member, send = _member_with_dm()
    monkeypatch.setattr(cron, "guild_member", AsyncMock(return_value=member))
    participant = _participant(current_week=TOTAL_WEEKS, week_completed=True)

    result = await cron._advance_participant(
        MagicMock(), _guild(), 12345, participant, {}
    )

    assert result is False
    assert participant.current_week == TOTAL_WEEKS
    assert participant.week_completed is True
    send.assert_not_awaited()


async def test_advance_completed_participant_dms_next_prompt(monkeypatch):
    member, send = _member_with_dm()
    monkeypatch.setattr(cron, "guild_member", AsyncMock(return_value=member))
    participant = _participant(current_week=5, week_completed=True, reminder_sent=True)

    result = await cron._advance_participant(
        MagicMock(), _guild(), 12345, participant, {}
    )

    assert result is True
    assert participant.current_week == 6
    assert participant.week_completed is False
    assert participant.reminder_sent is False

    send.assert_awaited_once()
    embeds = send.await_args.kwargs["embeds"]
    assert "Week 6" in embeds[0].description


async def test_advance_queues_owner_prompt_copy(monkeypatch):
    member, _ = _member_with_dm()
    monkeypatch.setattr(cron, "guild_member", AsyncMock(return_value=member))
    participant = _participant(current_week=5, week_completed=True, owner_id=999)
    owner_prompts: dict[int, list[tuple[int, object]]] = {}

    await cron._advance_participant(
        MagicMock(), _guild(), 12345, participant, owner_prompts
    )

    assert list(owner_prompts) == [999]
    sub_uid, prompt = owner_prompts[999][0]
    assert sub_uid == 12345
    assert prompt.week == 6


async def test_advance_departed_member_returns_remove_sentinel(monkeypatch):
    monkeypatch.setattr(cron, "guild_member", AsyncMock(return_value=None))
    participant = _participant(current_week=5, week_completed=True)

    result = await cron._advance_participant(
        MagicMock(), _guild(), 12345, participant, {}
    )

    assert result is None
    assert participant.current_week == 5
    assert participant.week_completed is True


async def test_advance_survives_dm_forbidden(monkeypatch):
    member, send = _member_with_dm()
    send.side_effect = hikari.ForbiddenError(url="", headers={}, raw_body=b"")
    monkeypatch.setattr(cron, "guild_member", AsyncMock(return_value=member))
    participant = _participant(current_week=5, week_completed=True)

    result = await cron._advance_participant(
        MagicMock(), _guild(), 12345, participant, {}
    )

    assert result is True
    assert participant.current_week == 6


# ---------------------------------------------------------------------------- #
#                        _cleanup_removed_participants                         #
# ---------------------------------------------------------------------------- #


def test_cleanup_deletes_removed_participants():
    guild_state = SubDayGuildState(guild_id=GUILD_ID, guild_name="Test Guild")
    guild_state.participants[1] = _participant(user_id=1)
    guild_state.participants[2] = _participant(user_id=2)

    cron._cleanup_removed_participants(guild_state, [1])

    assert list(guild_state.participants) == [2]


def test_cleanup_clears_references_to_removed_users():
    guild_state = SubDayGuildState(guild_id=GUILD_ID, guild_name="Test Guild")
    guild_state.participants[1] = _participant(user_id=1)
    guild_state.participants[2] = _participant(user_id=2, owner_id=1)
    guild_state.participants[3] = _participant(user_id=3, pending_owner_id=1)

    cron._cleanup_removed_participants(guild_state, [1])

    assert guild_state.participants[2].owner_id is None
    assert guild_state.participants[3].pending_owner_id is None


def test_cleanup_keeps_references_to_surviving_users():
    guild_state = SubDayGuildState(guild_id=GUILD_ID, guild_name="Test Guild")
    guild_state.participants[1] = _participant(user_id=1)
    guild_state.participants[2] = _participant(
        user_id=2, owner_id=7, pending_owner_id=8
    )

    cron._cleanup_removed_participants(guild_state, [1])

    assert guild_state.participants[2].owner_id == 7
    assert guild_state.participants[2].pending_owner_id == 8


# ---------------------------------------------------------------------------- #
#                            Owner request buttons                             #
# ---------------------------------------------------------------------------- #


def _save_participant(participant: SubDayParticipant) -> None:
    state.save(
        SubDayGuildState(
            guild_id=GUILD_ID,
            guild_name="Test Guild",
            participants={participant.user_id: participant},
        )
    )


def _owner_interaction(owner_id: int, action: str, sub_id: int) -> MagicMock:
    interaction = MagicMock()
    interaction.custom_id = f"{SUBDAY_OWNER_REQUEST_PREFIX}{action}:{GUILD_ID}:{sub_id}"
    interaction.user.id = owner_id
    interaction.create_initial_response = AsyncMock()

    bot = interaction.app
    bot.rest.fetch_member = AsyncMock()
    dm = MagicMock()
    dm.send = AsyncMock()
    sub_user = MagicMock()
    sub_user.fetch_dm_channel = AsyncMock(return_value=dm)
    bot.rest.fetch_user = AsyncMock(return_value=sub_user)
    bot.cache.get_guild = MagicMock(return_value=None)
    bot.state = MagicMock(return_value=None)
    return interaction


def _response_content(interaction: MagicMock) -> str:
    return interaction.create_initial_response.await_args.kwargs["content"]


def _reload_participant(sub_id: int) -> SubDayParticipant:
    state.store.cache.clear()
    return state.load(GUILD_ID).participants[sub_id]


async def test_owner_accept_sets_owner_and_persists():
    _save_participant(_participant(user_id=12345, pending_owner_id=999))
    interaction = _owner_interaction(999, "approve", 12345)

    await commands.handle_owner_interaction(interaction)

    saved = _reload_participant(12345)
    assert saved.owner_id == 999
    assert saved.pending_owner_id is None
    assert "accepted" in _response_content(interaction).lower()


async def test_owner_accept_notifies_the_sub():
    _save_participant(_participant(user_id=12345, pending_owner_id=999))
    interaction = _owner_interaction(999, "approve", 12345)

    await commands.handle_owner_interaction(interaction)

    interaction.app.rest.fetch_user.assert_awaited_once()
    dm_send = (
        await interaction.app.rest.fetch_user.return_value.fetch_dm_channel()
    ).send
    dm_send.assert_awaited_once()
    assert "accepted" in dm_send.await_args.args[0]


async def test_owner_accept_stale_button_rejected():
    _save_participant(_participant(user_id=12345, pending_owner_id=555))
    interaction = _owner_interaction(999, "approve", 12345)

    await commands.handle_owner_interaction(interaction)

    saved = _reload_participant(12345)
    assert saved.owner_id is None
    assert saved.pending_owner_id == 555
    assert "isn't valid anymore" in _response_content(interaction)


async def test_owner_accept_twice_is_idempotent():
    _save_participant(_participant(user_id=12345, owner_id=999))
    interaction = _owner_interaction(999, "approve", 12345)

    await commands.handle_owner_interaction(interaction)

    saved = _reload_participant(12345)
    assert saved.owner_id == 999
    assert "already their owner" in _response_content(interaction)
    interaction.app.rest.fetch_user.assert_not_awaited()


async def test_owner_deny_clears_pending():
    _save_participant(_participant(user_id=12345, pending_owner_id=999))
    interaction = _owner_interaction(999, "deny", 12345)

    await commands.handle_owner_interaction(interaction)

    saved = _reload_participant(12345)
    assert saved.pending_owner_id is None
    assert saved.owner_id is None
    assert "declined" in _response_content(interaction).lower()


async def test_owner_button_for_unknown_participant():
    _save_participant(_participant(user_id=12345, pending_owner_id=999))
    interaction = _owner_interaction(999, "approve", 6789)

    await commands.handle_owner_interaction(interaction)

    assert "isn't in the program" in _response_content(interaction)


async def test_owner_accept_when_owner_left_guild_cancels_request():
    _save_participant(_participant(user_id=12345, pending_owner_id=999))
    interaction = _owner_interaction(999, "approve", 12345)
    interaction.app.rest.fetch_member.side_effect = hikari.NotFoundError(
        url="", headers={}, raw_body=b""
    )

    await commands.handle_owner_interaction(interaction)

    saved = _reload_participant(12345)
    assert saved.owner_id is None
    assert saved.pending_owner_id is None
    assert "no longer in that server" in _response_content(interaction)
