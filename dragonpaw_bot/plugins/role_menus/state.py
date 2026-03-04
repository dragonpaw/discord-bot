# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from pathlib import Path

import pydantic
import safer
import yaml

from dragonpaw_bot.plugins.role_menus.models import RoleMenuGuildState

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
STATE_DIR = ROOT_DIR / "state"

_cache: dict[int, RoleMenuGuildState] = {}


def _state_path(guild_id: int) -> Path:
    return STATE_DIR / f"role_menus_{guild_id}.yaml"


def load(guild_id: int) -> RoleMenuGuildState:
    """Load guild state from cache or disk. Returns empty state if none exists."""
    if guild_id in _cache:
        return _cache[guild_id]

    path = _state_path(guild_id)
    if not path.exists():
        st = RoleMenuGuildState(guild_id=guild_id)
        _cache[guild_id] = st
        return st

    logger.debug("Loading role menu state from: %s", path)

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        logger.exception("G=%d: Failed to read role menu state file %s", guild_id, path)
        raise

    if not data:
        st = RoleMenuGuildState(guild_id=guild_id)
        _cache[guild_id] = st
        return st

    try:
        st = RoleMenuGuildState.model_validate(data)
    except pydantic.ValidationError:
        logger.exception(
            "G=%d: Role menu state validation failed for %s", guild_id, path
        )
        raise

    _cache[guild_id] = st
    return st


def save(guild_state: RoleMenuGuildState) -> None:
    """Save guild state to disk and update cache."""
    path = _state_path(guild_state.guild_id)
    logger.info("G=%r: Saving role menu state to: %s", guild_state.guild_name, path)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with safer.open(path, "w") as f:
            yaml.dump(
                guild_state.model_dump(mode="json"),
                f,
                default_flow_style=False,
                allow_unicode=True,
            )
    except Exception:
        logger.exception(
            "G=%r: FAILED to save role menu state to %s",
            guild_state.guild_name,
            path,
        )
        raise
    _cache[guild_state.guild_id] = guild_state
