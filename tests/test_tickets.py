from unittest.mock import AsyncMock, MagicMock

import hikari

from dragonpaw_bot.plugins.tickets import state as tickets_state
from dragonpaw_bot.plugins.tickets.commands import (
    _find_open_ticket,
    _sanitize_channel_name,
    handle_ticket_add_person_select,
)
from dragonpaw_bot.plugins.tickets.models import OpenTicket, TicketGuildState


def test_open_ticket_fields():
    t = OpenTicket(user_id=1, channel_id=2, topic="broken roles")
    assert t.user_id == 1
    assert t.channel_id == 2
    assert t.topic == "broken roles"


def test_ticket_guild_state_defaults():
    st = TicketGuildState(guild_id=100)
    assert st.guild_id == 100
    assert st.guild_name == ""
    assert st.category_id is None
    assert st.staff_role_id is None
    assert st.required_role_id is None
    assert st.open_tickets == []


def test_ticket_guild_state_with_tickets():
    ticket = OpenTicket(user_id=10, channel_id=20, topic="help me")
    st = TicketGuildState(guild_id=100, open_tickets=[ticket])
    assert len(st.open_tickets) == 1
    assert st.open_tickets[0].user_id == 10


def test_ticket_guild_state_round_trip():
    ticket = OpenTicket(user_id=10, channel_id=20, topic="help me")
    st = TicketGuildState(
        guild_id=100,
        guild_name="Test Guild",
        category_id=500,
        staff_role_id=600,
        required_role_id=700,
        open_tickets=[ticket],
    )
    data = st.model_dump(mode="json")
    loaded = TicketGuildState.model_validate(data)
    assert loaded.guild_id == 100
    assert loaded.category_id == 500
    assert loaded.staff_role_id == 600
    assert loaded.required_role_id == 700
    assert len(loaded.open_tickets) == 1
    assert loaded.open_tickets[0].topic == "help me"


def test_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(tickets_state, "STATE_DIR", tmp_path)
    tickets_state._cache.clear()

    st = TicketGuildState(
        guild_id=200,
        guild_name="Test Guild",
        staff_role_id=999,
        open_tickets=[OpenTicket(user_id=1, channel_id=2, topic="halp")],
    )
    tickets_state.save(st)
    tickets_state._cache.clear()

    loaded = tickets_state.load(200)
    assert loaded.guild_id == 200
    assert loaded.guild_name == "Test Guild"
    assert loaded.staff_role_id == 999
    assert len(loaded.open_tickets) == 1
    assert loaded.open_tickets[0].topic == "halp"


def test_state_load_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tickets_state, "STATE_DIR", tmp_path)
    tickets_state._cache.clear()

    loaded = tickets_state.load(999)
    assert loaded.guild_id == 999
    assert loaded.open_tickets == []


def test_state_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(tickets_state, "STATE_DIR", tmp_path)
    tickets_state._cache.clear()

    st = TicketGuildState(guild_id=300, guild_name="Cached")
    tickets_state.save(st)

    first = tickets_state.load(300)
    second = tickets_state.load(300)
    assert first is second


def test_state_round_trip_no_tickets(tmp_path, monkeypatch):
    monkeypatch.setattr(tickets_state, "STATE_DIR", tmp_path)
    tickets_state._cache.clear()

    st = TicketGuildState(guild_id=400)
    tickets_state.save(st)
    tickets_state._cache.clear()

    loaded = tickets_state.load(400)
    assert loaded.open_tickets == []


def test_sanitize_simple_name():
    assert _sanitize_channel_name("Alice") == "help-alice"


def test_sanitize_spaces_become_hyphens():
    assert _sanitize_channel_name("John Smith") == "help-john-smith"


def test_sanitize_strips_special_chars():
    assert _sanitize_channel_name("User#1234") == "help-user-1234"


def test_sanitize_emoji_stripped():
    assert _sanitize_channel_name("Cool 🐉 User") == "help-cool-user"


def test_sanitize_collapses_multiple_hyphens():
    assert _sanitize_channel_name("Cool  🐉  User") == "help-cool-user"


def test_sanitize_strips_leading_trailing_hyphens():
    assert _sanitize_channel_name("###Alice###") == "help-alice"


def test_sanitize_truncated_to_100_chars():
    long_name = "a" * 200
    result = _sanitize_channel_name(long_name)
    assert len(result) <= 100
    assert result.startswith("help-")


def _add_person_interaction(calls: list[str]) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = 100
    interaction.values = ["555"]
    interaction.channel_id = 42
    interaction.create_initial_response = AsyncMock(
        side_effect=lambda *a, **k: calls.append("initial_response")
    )
    interaction.edit_initial_response = AsyncMock(
        side_effect=lambda *a, **k: calls.append("edit_response")
    )
    bot = interaction.app
    bot.rest.edit_permission_overwrite = AsyncMock(
        side_effect=lambda *a, **k: calls.append("edit_permission_overwrite")
    )
    bot.rest.create_message = AsyncMock(
        side_effect=lambda *a, **k: calls.append("create_message")
    )
    return interaction


async def test_add_person_select_responds_before_rest_work():
    calls: list[str] = []
    interaction = _add_person_interaction(calls)

    await handle_ticket_add_person_select(interaction)

    assert calls[0] == "initial_response"
    assert "edit_permission_overwrite" in calls
    assert calls[-1] == "edit_response"


async def test_add_person_select_forbidden_reports_error():
    calls: list[str] = []
    interaction = _add_person_interaction(calls)
    interaction.app.rest.edit_permission_overwrite.side_effect = hikari.ForbiddenError(
        url="", headers={}, raw_body=b""
    )

    await handle_ticket_add_person_select(interaction)

    assert calls[0] == "initial_response"
    interaction.edit_initial_response.assert_awaited_once()
    interaction.app.rest.create_message.assert_not_awaited()


def test_find_open_ticket_found():
    st = TicketGuildState(
        guild_id=1, open_tickets=[OpenTicket(user_id=10, channel_id=20, topic="t")]
    )
    ticket = _find_open_ticket(st, 10)
    assert ticket is not None and ticket.channel_id == 20


def test_find_open_ticket_absent():
    st = TicketGuildState(guild_id=1)
    assert _find_open_ticket(st, 10) is None
