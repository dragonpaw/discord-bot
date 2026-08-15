from __future__ import annotations

from dragonpaw_bot.plugins.birthdays.models import BirthdayGuildState
from dragonpaw_bot.state_store import GuildStateStore

store = GuildStateStore("birthdays", BirthdayGuildState)
load = store.load
save = store.save
