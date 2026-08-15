from __future__ import annotations

from dragonpaw_bot.plugins.subday.models import SubDayGuildState
from dragonpaw_bot.state_store import GuildStateStore

store = GuildStateStore("subday", SubDayGuildState)
load = store.load
save = store.save


def is_configured(guild_id: int) -> bool:
    """Whether this guild has any persisted SubDay state on disk."""
    return store.path(guild_id).exists()
