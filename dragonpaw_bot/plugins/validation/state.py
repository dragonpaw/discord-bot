from __future__ import annotations

from pathlib import Path

import pydantic
import safer
import structlog
import yaml

from dragonpaw_bot.plugins.validation.models import ValidationGuildState

logger = structlog.get_logger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
STATE_DIR = ROOT_DIR / "state"

_cache: dict[int, ValidationGuildState] = {}


def _state_path(guild_id: int) -> Path:
    return STATE_DIR / f"validation_{guild_id}.yaml"


def load(guild_id: int) -> ValidationGuildState:
    """Load guild state from cache or disk. Returns empty state if none exists."""
    if guild_id in _cache:
        return _cache[guild_id]

    path = _state_path(guild_id)
    if not path.exists():
        st = ValidationGuildState(guild_id=guild_id, guild_name=str(guild_id))
        _cache[guild_id] = st
        return st

    logger.debug("Loading validation state", guild_id=guild_id, path=str(path))

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        logger.exception(
            "Failed to read validation state file", guild_id=guild_id, path=str(path)
        )
        raise

    if not data:
        st = ValidationGuildState(guild_id=guild_id, guild_name=str(guild_id))
        _cache[guild_id] = st
        return st

    try:
        st = ValidationGuildState.model_validate(data)
    except pydantic.ValidationError:
        logger.exception(
            "Validation state validation failed", guild_id=guild_id, path=str(path)
        )
        raise

    _cache[guild_id] = st
    return st


def save(guild_state: ValidationGuildState) -> None:
    """Save guild state to disk and update cache."""
    path = _state_path(guild_state.guild_id)
    logger.debug(
        "Saving validation state", guild=guild_state.guild_name, path=str(path)
    )
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
            "FAILED to save validation state",
            guild=guild_state.guild_name,
            path=str(path),
        )
        raise
    _cache[guild_state.guild_id] = guild_state
