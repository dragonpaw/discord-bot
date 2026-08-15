from __future__ import annotations

from dragonpaw_bot.plugins.intros.models import IntrosGuildState
from dragonpaw_bot.state_store import GuildStateStore

store = GuildStateStore("intros", IntrosGuildState)
load = store.load
save = store.save
