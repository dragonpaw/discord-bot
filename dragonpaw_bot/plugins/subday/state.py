from __future__ import annotations

from pathlib import Path

import safer
import structlog
import yaml

from dragonpaw_bot.plugins.subday.models import SubDayGuildState

logger = structlog.get_logger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
STATE_DIR = ROOT_DIR / "state"

_cache: dict[int, SubDayGuildState] = {}


def _state_path(guild_id: int) -> Path:
    return STATE_DIR / f"subday_{guild_id}.yaml"


def load(guild_id: int) -> SubDayGuildState:
    """Load guild state from cache or disk. Returns empty state if none exists."""
    if guild_id in _cache:
        return _cache[guild_id]

    path = _state_path(guild_id)
    if path.exists():
        logger.debug("Loading subday state", guild_id=guild_id, path=str(path))
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            if data:
                state = SubDayGuildState.model_validate(data)
                _cache[guild_id] = state
                return state
        except Exception:
            logger.exception(
                "Error loading subday state, starting fresh",
                guild_id=guild_id,
                path=str(path),
            )

    state = SubDayGuildState(guild_id=guild_id)
    _cache[guild_id] = state
    return state


def save(state: SubDayGuildState) -> None:
    """Save guild state to disk and update cache."""
    _cache[state.guild_id] = state
    path = _state_path(state.guild_id)
    logger.info("Saving subday state", guild=state.guild_name, path=str(path))
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with safer.open(path, "w") as f:
        yaml.dump(
            state.model_dump(mode="json"),
            f,
            default_flow_style=False,
            allow_unicode=True,
        )
