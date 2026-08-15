from __future__ import annotations

from dragonpaw_bot.plugins.validation.models import ValidationGuildState
from dragonpaw_bot.state_store import GuildStateStore

store = GuildStateStore("validation", ValidationGuildState)
load = store.load
save = store.save


def all_guild_ids() -> list[int]:
    """Return all guild IDs that have persisted validation state on disk."""
    return [
        int(p.stem.removeprefix("validation_"))
        for p in store.state_dir.glob("validation_*.yaml")
    ]
