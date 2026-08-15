from __future__ import annotations

from dragonpaw_bot.plugins.tickets.models import TicketGuildState
from dragonpaw_bot.state_store import GuildStateStore

store = GuildStateStore("tickets", TicketGuildState)
load = store.load
save = store.save
