from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import hikari

from dragonpaw_bot.plugins.tickets import state as tickets_state
from dragonpaw_bot.plugins.tickets.commands import (
    TOPIC_INPUT_ID,
    _find_open_ticket,
    _sanitize_channel_name,
    _ticket_block_reason,
    handle_ticket_add_person_select,
    handle_ticket_close_confirm,
    handle_topic_modal,
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
    monkeypatch.setattr(tickets_state.store, "state_dir", tmp_path)
    tickets_state.store.cache.clear()

    st = TicketGuildState(
        guild_id=200,
        guild_name="Test Guild",
        staff_role_id=999,
        open_tickets=[OpenTicket(user_id=1, channel_id=2, topic="halp")],
    )
    tickets_state.save(st)
    tickets_state.store.cache.clear()

    loaded = tickets_state.load(200)
    assert loaded.guild_id == 200
    assert loaded.guild_name == "Test Guild"
    assert loaded.staff_role_id == 999
    assert len(loaded.open_tickets) == 1
    assert loaded.open_tickets[0].topic == "halp"


def test_state_load_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tickets_state.store, "state_dir", tmp_path)
    tickets_state.store.cache.clear()

    loaded = tickets_state.load(999)
    assert loaded.guild_id == 999
    assert loaded.open_tickets == []


def test_state_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(tickets_state.store, "state_dir", tmp_path)
    tickets_state.store.cache.clear()

    st = TicketGuildState(guild_id=300, guild_name="Cached")
    tickets_state.save(st)

    first = tickets_state.load(300)
    second = tickets_state.load(300)
    assert first is second


def test_state_round_trip_no_tickets(tmp_path, monkeypatch):
    monkeypatch.setattr(tickets_state.store, "state_dir", tmp_path)
    tickets_state.store.cache.clear()

    st = TicketGuildState(guild_id=400)
    tickets_state.save(st)
    tickets_state.store.cache.clear()

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


# ---------------------------------------------------------------------------- #
#                             _ticket_block_reason                              #
# ---------------------------------------------------------------------------- #

GUILD_ID = hikari.Snowflake(100)
USER_ID = hikari.Snowflake(42)
LOG_CHANNEL_ID = hikari.Snowflake(777)
NEW_CHANNEL_ID = hikari.Snowflake(555)
STAFF_ROLE_ID = 600
CATEGORY_ID = 500


def _member(*, role_ids: list[int] | None = None, display_name: str = "Alice"):
    member = MagicMock(spec=hikari.Member)
    member.role_ids = [hikari.Snowflake(r) for r in (role_ids or [])]
    member.display_name = display_name
    return member


def test_block_reason_required_role_missing():
    st = TicketGuildState(guild_id=100, required_role_id=700)
    reason = _ticket_block_reason(st, _member(role_ids=[1]), USER_ID)
    assert reason is not None
    assert "allowed to open a ticket" in reason


def test_block_reason_required_role_present():
    st = TicketGuildState(guild_id=100, required_role_id=700)
    assert _ticket_block_reason(st, _member(role_ids=[700]), USER_ID) is None


def test_block_reason_duplicate_ticket_points_at_channel():
    st = TicketGuildState(
        guild_id=100,
        open_tickets=[OpenTicket(user_id=int(USER_ID), channel_id=20, topic="halp")],
    )
    reason = _ticket_block_reason(st, _member(), USER_ID)
    assert reason is not None
    assert "<#20>" in reason


def test_block_reason_other_users_ticket_does_not_block():
    st = TicketGuildState(
        guild_id=100,
        open_tickets=[OpenTicket(user_id=999, channel_id=20, topic="halp")],
    )
    assert _ticket_block_reason(st, _member(), USER_ID) is None


def test_block_reason_all_clear():
    st = TicketGuildState(guild_id=100)
    assert _ticket_block_reason(st, _member(), USER_ID) is None


def test_block_reason_role_gate_checked_before_duplicate():
    """A roleless member with an open ticket hears about the role, not the ticket."""
    st = TicketGuildState(
        guild_id=100,
        required_role_id=700,
        open_tickets=[OpenTicket(user_id=int(USER_ID), channel_id=20, topic="halp")],
    )
    reason = _ticket_block_reason(st, _member(), USER_ID)
    assert reason is not None
    assert "<#20>" not in reason


# ---------------------------------------------------------------------------- #
#                              handle_topic_modal                               #
# ---------------------------------------------------------------------------- #


def _modal_bot(*, admin: bool = True, calls: list[str] | None = None) -> MagicMock:
    """Bot mock wired so the real GuildContext / permission helpers run against it.

    Only the REST and cache boundaries are faked; everything above them is real
    plugin code.
    """
    bot = MagicMock()
    bot.user_id = hikari.Snowflake(999)

    guild = MagicMock()
    guild.name = "Test Guild"
    bot.cache.get_guild = MagicMock(return_value=guild)
    bot.state = MagicMock(
        return_value=SimpleNamespace(log_channel_id=LOG_CHANNEL_ID),
    )

    me = MagicMock()
    me.role_ids = []
    bot.cache.get_member = MagicMock(return_value=me)

    everyone = MagicMock()
    everyone.permissions = (
        hikari.Permissions.ADMINISTRATOR if admin else hikari.Permissions.NONE
    )
    bot.cache.get_roles_view_for_guild = MagicMock(return_value={GUILD_ID: everyone})

    channel = MagicMock()
    channel.id = NEW_CHANNEL_ID
    record = calls if calls is not None else []
    bot.rest.create_guild_text_channel = AsyncMock(
        side_effect=lambda *a, **k: record.append("create_channel") or channel
    )
    bot.rest.create_message = AsyncMock(
        side_effect=lambda *a, **k: record.append("create_message")
    )
    bot.rest.delete_channel = AsyncMock(
        side_effect=lambda *a, **k: record.append("delete_channel")
    )
    return bot


def _modal_interaction(
    *, topic: str | None = "broken roles", calls: list[str] | None = None
) -> MagicMock:
    record = calls if calls is not None else []
    interaction = MagicMock()
    interaction.app = _modal_bot(calls=record)
    interaction.guild_id = GUILD_ID
    interaction.member = _member()
    interaction.user.id = USER_ID

    component = MagicMock()
    component.custom_id = TOPIC_INPUT_ID if topic is not None else "some_other_input"
    component.value = topic or ""
    row = MagicMock()
    row.components = [component]
    interaction.components = [row]

    interaction.create_initial_response = AsyncMock(
        side_effect=lambda *a, **k: record.append("initial_response")
    )
    interaction.edit_initial_response = AsyncMock(
        side_effect=lambda *a, **k: record.append("edit_response")
    )
    return interaction


def _tickets_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tickets_state.store, "state_dir", tmp_path)
    tickets_state.store.cache.clear()


async def test_topic_modal_creates_channel_and_saves_ticket(tmp_path, monkeypatch):
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(
        TicketGuildState(
            guild_id=int(GUILD_ID),
            category_id=CATEGORY_ID,
            staff_role_id=STAFF_ROLE_ID,
        )
    )
    interaction = _modal_interaction()

    await handle_topic_modal(interaction)

    kwargs = interaction.app.rest.create_guild_text_channel.await_args.kwargs
    assert kwargs["name"] == "help-alice"
    assert kwargs["category"] == hikari.Snowflake(CATEGORY_ID)

    st = tickets_state.load(int(GUILD_ID))
    assert len(st.open_tickets) == 1
    assert st.open_tickets[0].user_id == int(USER_ID)
    assert st.open_tickets[0].channel_id == int(NEW_CHANNEL_ID)
    assert st.open_tickets[0].topic == "broken roles"

    interaction.edit_initial_response.assert_awaited_once()
    assert (
        f"<#{NEW_CHANNEL_ID}>"
        in interaction.edit_initial_response.await_args.kwargs["content"]
    )


async def test_topic_modal_persists_ticket_across_reload(tmp_path, monkeypatch):
    """The saved ticket survives a cache-clearing reload, so it really hit disk."""
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(
        TicketGuildState(guild_id=int(GUILD_ID), staff_role_id=STAFF_ROLE_ID)
    )

    await handle_topic_modal(_modal_interaction())

    tickets_state.store.cache.clear()
    st = tickets_state.load(int(GUILD_ID))
    assert [t.channel_id for t in st.open_tickets] == [int(NEW_CHANNEL_ID)]


async def test_topic_modal_pings_staff_role_in_ticket_channel(tmp_path, monkeypatch):
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(
        TicketGuildState(guild_id=int(GUILD_ID), staff_role_id=STAFF_ROLE_ID)
    )
    interaction = _modal_interaction()

    await handle_topic_modal(interaction)

    posts = [
        c
        for c in interaction.app.rest.create_message.await_args_list
        if c.kwargs.get("channel") == NEW_CHANNEL_ID
    ]
    assert len(posts) == 1
    content = posts[0].kwargs["content"]
    assert f"<@&{STAFF_ROLE_ID}>" in content
    assert "broken roles" in content
    assert "Alice" in content


async def test_topic_modal_logs_to_guild_log_channel(tmp_path, monkeypatch):
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(TicketGuildState(guild_id=int(GUILD_ID)))
    interaction = _modal_interaction()

    await handle_topic_modal(interaction)

    logged = [
        c
        for c in interaction.app.rest.create_message.await_args_list
        if c.kwargs.get("channel") == LOG_CHANNEL_ID
    ]
    assert len(logged) == 1
    assert "broken roles" in logged[0].kwargs["content"]


async def test_topic_modal_defers_before_creating_channel(tmp_path, monkeypatch):
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(TicketGuildState(guild_id=int(GUILD_ID)))
    calls: list[str] = []
    interaction = _modal_interaction(calls=calls)

    await handle_topic_modal(interaction)

    assert calls[0] == "initial_response"
    assert calls.index("create_channel") > 0
    assert (
        interaction.create_initial_response.await_args.kwargs["response_type"]
        == hikari.ResponseType.DEFERRED_MESSAGE_CREATE
    )


async def test_topic_modal_missing_topic_responds_with_retry(tmp_path, monkeypatch):
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(TicketGuildState(guild_id=int(GUILD_ID)))
    interaction = _modal_interaction(topic=None)

    await handle_topic_modal(interaction)

    interaction.create_initial_response.assert_awaited_once()
    kwargs = interaction.create_initial_response.await_args.kwargs
    assert kwargs["response_type"] == hikari.ResponseType.MESSAGE_CREATE
    assert "didn't catch a topic" in kwargs["content"]
    interaction.app.rest.create_guild_text_channel.assert_not_awaited()
    assert tickets_state.load(int(GUILD_ID)).open_tickets == []


async def test_topic_modal_race_guard_skips_second_channel(tmp_path, monkeypatch):
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(
        TicketGuildState(
            guild_id=int(GUILD_ID),
            open_tickets=[
                OpenTicket(user_id=int(USER_ID), channel_id=20, topic="first")
            ],
        )
    )
    interaction = _modal_interaction()

    await handle_topic_modal(interaction)

    interaction.app.rest.create_guild_text_channel.assert_not_awaited()
    assert "<#20>" in interaction.edit_initial_response.await_args.kwargs["content"]
    assert len(tickets_state.load(int(GUILD_ID)).open_tickets) == 1


async def test_topic_modal_missing_perms_reports_and_skips_creation(
    tmp_path, monkeypatch
):
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(TicketGuildState(guild_id=int(GUILD_ID)))
    interaction = _modal_interaction()
    interaction.app = _modal_bot(admin=False)

    await handle_topic_modal(interaction)

    interaction.app.rest.create_guild_text_channel.assert_not_awaited()
    assert tickets_state.load(int(GUILD_ID)).open_tickets == []
    assert (
        "missing permissions"
        in interaction.edit_initial_response.await_args.kwargs["content"]
    )
    logged = [
        c
        for c in interaction.app.rest.create_message.await_args_list
        if c.kwargs.get("channel") == LOG_CHANNEL_ID
    ]
    assert len(logged) == 1
    assert "Manage Channels" in logged[0].kwargs["content"]


async def test_topic_modal_channel_creation_failure_reports_error(
    tmp_path, monkeypatch
):
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(TicketGuildState(guild_id=int(GUILD_ID)))
    interaction = _modal_interaction()
    interaction.app.rest.create_guild_text_channel.side_effect = hikari.ForbiddenError(
        url="", headers={}, raw_body=b""
    )

    await handle_topic_modal(interaction)

    assert tickets_state.load(int(GUILD_ID)).open_tickets == []
    assert (
        "couldn't open a ticket channel"
        in interaction.edit_initial_response.await_args.kwargs["content"]
    )


# ---------------------------------------------------------------------------- #
#                          handle_ticket_close_confirm                          #
# ---------------------------------------------------------------------------- #


def _close_interaction(*, custom_id: str = "ticket_close_confirm:20") -> MagicMock:
    interaction = MagicMock()
    interaction.app = _modal_bot()
    interaction.app.cache.get_member = MagicMock(return_value=_member())
    interaction.guild_id = GUILD_ID
    interaction.member = _member(display_name="Bob")
    interaction.custom_id = custom_id
    interaction.create_initial_response = AsyncMock()
    return interaction


async def test_close_confirm_deletes_channel_and_drops_ticket(tmp_path, monkeypatch):
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(
        TicketGuildState(
            guild_id=int(GUILD_ID),
            open_tickets=[
                OpenTicket(user_id=int(USER_ID), channel_id=20, topic="halp"),
                OpenTicket(user_id=999, channel_id=21, topic="other"),
            ],
        )
    )
    interaction = _close_interaction()

    await handle_ticket_close_confirm(interaction)

    interaction.app.rest.delete_channel.assert_awaited_once_with(20)

    tickets_state.store.cache.clear()
    st = tickets_state.load(int(GUILD_ID))
    assert [t.channel_id for t in st.open_tickets] == [21]


async def test_close_confirm_logs_opener_and_closer(tmp_path, monkeypatch):
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(
        TicketGuildState(
            guild_id=int(GUILD_ID),
            open_tickets=[
                OpenTicket(user_id=int(USER_ID), channel_id=20, topic="halp")
            ],
        )
    )
    interaction = _close_interaction()

    await handle_ticket_close_confirm(interaction)

    logged = [
        c
        for c in interaction.app.rest.create_message.await_args_list
        if c.kwargs.get("channel") == LOG_CHANNEL_ID
    ]
    assert len(logged) == 1
    assert "Alice" in logged[0].kwargs["content"]
    assert "Bob" in logged[0].kwargs["content"]


async def test_close_confirm_acks_before_deleting(tmp_path, monkeypatch):
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(TicketGuildState(guild_id=int(GUILD_ID)))
    interaction = _close_interaction()

    await handle_ticket_close_confirm(interaction)

    interaction.create_initial_response.assert_awaited_once_with(
        response_type=hikari.ResponseType.DEFERRED_MESSAGE_UPDATE,
    )


async def test_close_confirm_unparseable_channel_id_does_nothing(tmp_path, monkeypatch):
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(
        TicketGuildState(
            guild_id=int(GUILD_ID),
            open_tickets=[
                OpenTicket(user_id=int(USER_ID), channel_id=20, topic="halp")
            ],
        )
    )
    interaction = _close_interaction(custom_id="ticket_close_confirm:nonsense")

    await handle_ticket_close_confirm(interaction)

    interaction.app.rest.delete_channel.assert_not_awaited()
    assert len(tickets_state.load(int(GUILD_ID)).open_tickets) == 1


async def test_close_confirm_untracked_channel_still_deletes(tmp_path, monkeypatch):
    _tickets_dir(tmp_path, monkeypatch)
    tickets_state.save(TicketGuildState(guild_id=int(GUILD_ID)))
    interaction = _close_interaction()

    await handle_ticket_close_confirm(interaction)

    interaction.app.rest.delete_channel.assert_awaited_once_with(20)
    logged = [
        c
        for c in interaction.app.rest.create_message.await_args_list
        if c.kwargs.get("channel") == LOG_CHANNEL_ID
    ]
    assert "someone" in logged[0].kwargs["content"]
