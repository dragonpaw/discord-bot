from __future__ import annotations

import structlog

from dragonpaw_bot.plugins.birthdays.models import BirthdayGuildState
from dragonpaw_bot.state_store import GuildStateStore

logger = structlog.get_logger(__name__)

store = GuildStateStore("birthdays", BirthdayGuildState)
save = store.save


def load(guild_id: int) -> BirthdayGuildState:
    st = store.load(guild_id)
    _migrate_wishlist_urls(st)
    return st


def _migrate_wishlist_urls(guild_state: BirthdayGuildState) -> None:
    """One-time migration: strip query params and clear invalid wishlist URLs."""
    changed = False
    for entry in guild_state.birthdays.values():
        if not entry.wishlist_url:
            continue
        if not entry.wishlist_url.startswith(("https://", "http://")):
            entry.wishlist_url = None
            changed = True
            continue
        cleaned = entry.wishlist_url.split("?", maxsplit=1)[0]
        if cleaned != entry.wishlist_url:
            entry.wishlist_url = cleaned
            changed = True
    if changed:
        logger.info(
            "Migrated wishlist URLs",
            guild=guild_state.guild_name,
            guild_id=guild_state.guild_id,
        )
        save(guild_state)
