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
