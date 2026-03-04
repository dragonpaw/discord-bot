# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from pathlib import Path

import safer
import yaml

from dragonpaw_bot.plugins.birthdays.models import BirthdayGuildState

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
STATE_DIR = ROOT_DIR / "state"

_cache: dict[int, BirthdayGuildState] = {}


def _state_path(guild_id: int) -> Path:
    return STATE_DIR / f"birthdays_{guild_id}.yaml"


def load(guild_id: int) -> BirthdayGuildState:
    """Load guild state from cache or disk. Returns empty state if none exists."""
    if guild_id in _cache:
        return _cache[guild_id]

    path = _state_path(guild_id)
    if path.exists():
        logger.debug("Loading birthday state from: %s", path)
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            if data:
                st = BirthdayGuildState.model_validate(data)
                _cache[guild_id] = st
                return st
        except Exception:
            logger.exception("Error loading birthday state, starting fresh")

    st = BirthdayGuildState(guild_id=guild_id)
    _cache[guild_id] = st
    return st


def save(state: BirthdayGuildState) -> None:
    """Save guild state to disk and update cache."""
    _cache[state.guild_id] = state
    path = _state_path(state.guild_id)
    logger.info("G=%r: Saving birthday state to: %s", state.guild_name, path)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with safer.open(path, "w") as f:
        yaml.dump(
            state.model_dump(mode="json"),
            f,
            default_flow_style=False,
            allow_unicode=True,
        )
