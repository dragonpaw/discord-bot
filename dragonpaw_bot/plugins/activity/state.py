from __future__ import annotations

from pathlib import Path

import pydantic
import safer
import structlog
import yaml

from dragonpaw_bot.plugins.activity.models import (
    ActivityGuildMeta,
    ActivityGuildState,
    UserActivity,
)

logger = structlog.get_logger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
STATE_DIR = ROOT_DIR / "state"

_config_cache: dict[int, ActivityGuildMeta] = {}
_user_cache: dict[tuple[int, int], UserActivity] = {}
_dirty_users: set[tuple[int, int]] = set()


def _config_path(guild_id: int) -> Path:
    return STATE_DIR / f"activity_config_{guild_id}.yaml"


def _user_path(guild_id: int, user_id: int) -> Path:
    return STATE_DIR / f"activity_user_{guild_id}_{user_id}.yaml"


def _old_combined_path(guild_id: int) -> Path:
    return STATE_DIR / f"activity_{guild_id}.yaml"


def _migrate(guild_id: int) -> ActivityGuildMeta:
    """Read old combined YAML, split into per-user files + config file, delete old file."""
    old_path = _old_combined_path(guild_id)
    try:
        with open(old_path) as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        logger.exception(
            "Failed to read legacy activity state",
            guild_id=guild_id,
            path=str(old_path),
        )
        raise

    if not data:
        old_path.unlink(missing_ok=True)
        return ActivityGuildMeta(guild_id=guild_id)

    try:
        old = ActivityGuildState.model_validate(data)
    except pydantic.ValidationError:
        logger.exception("Legacy activity state validation failed", guild_id=guild_id)
        raise

    meta = ActivityGuildMeta(
        guild_id=old.guild_id,
        guild_name=old.guild_name,
        config=old.config,
    )
    save_config(meta)

    for uid, ua in old.users.items():
        try:
            save_user(guild_id, uid, ua)
        except Exception:
            logger.exception(
                "Failed to migrate user activity — data may be lost",
                guild_id=guild_id,
                user_id=uid,
            )

    old_path.unlink()
    logger.debug(
        "Migrated legacy activity state",
        guild=old.guild_name,
        users=len(old.users),
    )
    return meta


def load_config(guild_id: int) -> ActivityGuildMeta:
    """Load guild config from cache or disk. Migrates old combined file if needed."""
    if guild_id in _config_cache:
        return _config_cache[guild_id]

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = _config_path(guild_id)

    if not config_path.exists():
        if _old_combined_path(guild_id).exists():
            meta = _migrate(guild_id)
            _config_cache[guild_id] = meta
            return meta
        meta = ActivityGuildMeta(guild_id=guild_id)
        _config_cache[guild_id] = meta
        return meta

    logger.debug("Loading activity config", guild_id=guild_id)
    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        logger.exception(
            "Failed to read activity config", guild_id=guild_id, path=str(config_path)
        )
        raise

    if not data:
        meta = ActivityGuildMeta(guild_id=guild_id)
        _config_cache[guild_id] = meta
        return meta

    try:
        meta = ActivityGuildMeta.model_validate(data)
    except pydantic.ValidationError:
        logger.exception("Activity config validation failed", guild_id=guild_id)
        raise

    _config_cache[guild_id] = meta
    return meta


def save_config(meta: ActivityGuildMeta) -> None:
    """Save guild config to disk and update cache."""
    path = _config_path(meta.guild_id)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logger.debug("Saving activity config", guild=meta.guild_name)
    try:
        with safer.open(path, "w") as f:
            yaml.dump(
                meta.model_dump(mode="json"),
                f,
                default_flow_style=False,
                allow_unicode=True,
            )
    except Exception:
        logger.exception(
            "FAILED to save activity config", guild=meta.guild_name, path=str(path)
        )
        raise
    _config_cache[meta.guild_id] = meta


def load_user(guild_id: int, user_id: int) -> UserActivity | None:
    """Load a single user's activity from cache or disk. Returns None if no data."""
    key = (guild_id, user_id)
    if key in _user_cache:
        return _user_cache[key]

    path = _user_path(guild_id, user_id)
    if not path.exists():
        return None

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        logger.exception(
            "Failed to read activity user file", guild_id=guild_id, user_id=user_id
        )
        raise

    if not data:
        return None

    try:
        ua = UserActivity.model_validate(data)
    except pydantic.ValidationError:
        logger.exception(
            "Activity user validation failed", guild_id=guild_id, user_id=user_id
        )
        raise

    _user_cache[key] = ua
    return ua


def save_user(guild_id: int, user_id: int, ua: UserActivity) -> None:
    """Save a single user's activity to disk and update cache."""
    path = _user_path(guild_id, user_id)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with safer.open(path, "w") as f:
            yaml.dump(
                ua.model_dump(mode="json"),
                f,
                default_flow_style=False,
                allow_unicode=True,
            )
    except Exception:
        logger.exception(
            "FAILED to save activity user", guild_id=guild_id, user_id=user_id
        )
        raise
    _user_cache[(guild_id, user_id)] = ua


def delete_user(guild_id: int, user_id: int) -> None:
    """Delete a user's activity file and remove from cache."""
    path = _user_path(guild_id, user_id)
    path.unlink(missing_ok=True)
    _user_cache.pop((guild_id, user_id), None)
    _dirty_users.discard((guild_id, user_id))


def list_user_ids(guild_id: int) -> list[int]:
    """Return all user IDs with stored activity files for this guild."""
    prefix = f"activity_user_{guild_id}_"
    result = []
    for p in STATE_DIR.glob(f"{prefix}*.yaml"):
        try:
            result.append(int(p.stem[len(prefix) :]))
        except ValueError:
            logger.warning("Unexpected file in state dir, skipping", path=str(p))
    return result


def mark_user_dirty(guild_id: int, user_id: int) -> None:
    """Mark a user's activity as needing a flush to disk."""
    _dirty_users.add((guild_id, user_id))


def flush_dirty() -> int:
    """Write all dirty user files to disk. Returns number of users successfully flushed."""
    flushed = 0
    for guild_id, user_id in list(_dirty_users):
        ua = _user_cache.get((guild_id, user_id))
        if ua is None:
            logger.warning(
                "Dirty user missing from cache — activity data may be lost",
                guild_id=guild_id,
                user_id=user_id,
            )
            _dirty_users.discard((guild_id, user_id))
            continue
        try:
            save_user(guild_id, user_id, ua)
            _dirty_users.discard((guild_id, user_id))
            flushed += 1
        except Exception:
            logger.exception(
                "Failed to flush dirty user — will retry next hour",
                guild_id=guild_id,
                user_id=user_id,
            )
    return flushed
