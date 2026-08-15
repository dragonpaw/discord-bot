from __future__ import annotations

from dragonpaw_bot.plugins.media_channels.models import MediaGuildState
from dragonpaw_bot.state_store import GuildStateStore

store = GuildStateStore("media_channels", MediaGuildState)
load = store.load
save = store.save
