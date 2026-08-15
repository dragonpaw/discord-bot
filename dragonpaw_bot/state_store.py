"""Shared per-guild YAML state persistence.

Every plugin's state module builds one GuildStateStore instead of hand-rolling
the cache/YAML/pydantic boilerplate.
"""

from __future__ import annotations

from pathlib import Path

import pydantic
import safer
import structlog
import yaml

logger = structlog.get_logger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_STATE_DIR = ROOT_DIR / "state"


class GuildStateBase(pydantic.BaseModel):
    """Base for per-guild state models: the two fields every store needs."""

    guild_id: int = pydantic.Field(gt=0)
    guild_name: str = ""


class GuildStateStore[StateT: GuildStateBase]:
    """Cache-backed YAML persistence for one plugin's per-guild state model."""

    def __init__(self, name: str, model: type[StateT]) -> None:
        self.name = name
        self.model = model
        self.state_dir = DEFAULT_STATE_DIR
        self.cache: dict[int, StateT] = {}

    def path(self, guild_id: int) -> Path:
        return self.state_dir / f"{self.name}_{guild_id}.yaml"

    def load(self, guild_id: int) -> StateT:
        """Load guild state from cache or disk. Returns empty state if none exists."""
        if guild_id in self.cache:
            return self.cache[guild_id]

        path = self.path(guild_id)
        data = None
        if path.exists():
            logger.debug(
                "Loading state", store=self.name, guild_id=guild_id, path=str(path)
            )
            try:
                with open(path) as f:
                    data = yaml.safe_load(f)
            except (OSError, yaml.YAMLError):
                logger.exception(
                    "Failed to read state file",
                    store=self.name,
                    guild_id=guild_id,
                    path=str(path),
                )
                raise

        if not data:
            st = self.model(guild_id=guild_id)
        else:
            try:
                st = self.model.model_validate(data)
            except pydantic.ValidationError:
                logger.exception(
                    "State validation failed",
                    store=self.name,
                    guild_id=guild_id,
                    path=str(path),
                )
                raise

        self.cache[guild_id] = st
        return st

    def save(self, guild_state: StateT) -> None:
        """Save guild state to disk and update cache."""
        path = self.path(guild_state.guild_id)
        logger.debug(
            "Saving state",
            store=self.name,
            guild=guild_state.guild_name,
            path=str(path),
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)
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
                "FAILED to save state",
                store=self.name,
                guild=guild_state.guild_name,
                path=str(path),
            )
            raise
        self.cache[guild_state.guild_id] = guild_state
