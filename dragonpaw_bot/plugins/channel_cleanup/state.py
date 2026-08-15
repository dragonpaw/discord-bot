from __future__ import annotations

from dragonpaw_bot.plugins.channel_cleanup.models import CleanupGuildState
from dragonpaw_bot.state_store import GuildStateStore

store = GuildStateStore("channel_cleanup", CleanupGuildState)
load = store.load
save = store.save
