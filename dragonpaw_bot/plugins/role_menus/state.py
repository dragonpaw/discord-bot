from __future__ import annotations

from dragonpaw_bot.plugins.role_menus.models import RoleMenuGuildState
from dragonpaw_bot.state_store import GuildStateStore

store = GuildStateStore("role_menus", RoleMenuGuildState)
load = store.load
save = store.save
