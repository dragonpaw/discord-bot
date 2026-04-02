"""Activity tracker models and score calculation."""

from __future__ import annotations

import math
import time

import pydantic

CONTRIBUTION_VALUES: dict[str, float] = {
    "text": 1.0,
    "media": 2.0,
    "reaction": 0.1,
    "vc": 0.1,  # per minute
}

BASE_HALF_LIFE = 7 * 24 * 3600  # 7 days in seconds
ACTIVITY_FLOOR = 0.1
PRUNE_DAYS = 30


class ContributionBucket(pydantic.BaseModel):
    """Hourly aggregate of contributions of the same kind."""

    hour: int  # Unix timestamp floored to hour boundary
    kind: str  # "text" | "media" | "reaction" | "vc"
    amount: float = pydantic.Field(gt=0)  # sum of contributions in this hour


class UserActivity(pydantic.BaseModel):
    user_id: int = pydantic.Field(gt=0)
    buckets: list[ContributionBucket] = []


class RoleConfig(pydantic.BaseModel):
    role_id: int = pydantic.Field(gt=0)
    role_name: str
    contribution_multiplier: float = pydantic.Field(default=1.0, gt=0)
    decay_multiplier: float = pydantic.Field(default=1.0, gt=0)
    ignored: bool = False


class ChannelConfig(pydantic.BaseModel):
    channel_id: int = pydantic.Field(gt=0)
    channel_name: str
    point_multiplier: float = pydantic.Field(default=1.0, gt=0)


class ActivityGuildConfig(pydantic.BaseModel):
    role_configs: list[RoleConfig] = []
    channel_configs: list[ChannelConfig] = []
    lurker_role_id: int | None = None
    lurker_role_name: str = ""


class ActivityGuildState(pydantic.BaseModel):
    guild_id: int = pydantic.Field(gt=0)
    guild_name: str = ""
    config: ActivityGuildConfig = pydantic.Field(default_factory=ActivityGuildConfig)
    users: dict[int, UserActivity] = {}  # user_id → UserActivity


def calculate_score(
    buckets: list[ContributionBucket],
    role_config: RoleConfig | None,
    now: float | None = None,
) -> float:
    """Compute participation score with exponential decay and log-weighted buckets.

    Older buckets decay toward zero. More recent activity extends the half-life.
    Role config applies contribution and decay multipliers.
    """
    if not buckets:
        return 0.0

    if now is None:
        now = time.time()

    contrib_mult = role_config.contribution_multiplier if role_config else 1.0
    decay_mult = role_config.decay_multiplier if role_config else 1.0

    # Activity bonus: more hourly buckets in the last 7 days → longer half-life
    week_ago = now - 7 * 24 * 3600
    recent_count = sum(1 for b in buckets if b.hour >= week_ago)
    activity_bonus = math.log(recent_count + 1)
    half_life = BASE_HALF_LIFE * (1 + activity_bonus) * decay_mult

    # Group by kind
    by_kind: dict[str, list[ContributionBucket]] = {}
    for b in buckets:
        by_kind.setdefault(b.kind, []).append(b)

    score = 0.0
    for kind, kind_buckets in by_kind.items():
        base_value = CONTRIBUTION_VALUES.get(kind, 1.0)
        kind_buckets.sort(
            key=lambda b: b.hour, reverse=True
        )  # newest first → highest log_weight
        for idx, bucket in enumerate(kind_buckets):
            log_weight = 1.0 / math.log(idx + 2)
            base_pts = base_value * bucket.amount * log_weight * contrib_mult
            decay = math.pow(0.5, (now - bucket.hour) / half_life)
            score += base_pts * decay

    return score


def best_role_config(
    role_ids: list[int], role_configs: list[RoleConfig]
) -> RoleConfig | None:
    """Return the non-ignored RoleConfig with the highest contribution_multiplier, or None."""
    config_by_id = {rc.role_id: rc for rc in role_configs}
    matched = [config_by_id[rid] for rid in role_ids if rid in config_by_id]
    non_ignored = [rc for rc in matched if not rc.ignored]
    if not non_ignored:
        return None
    return max(non_ignored, key=lambda rc: rc.contribution_multiplier)


def has_ignored_role(role_ids: list[int], role_configs: list[RoleConfig]) -> bool:
    """Return True if any of the member's roles is marked ignored."""
    ignored_ids = {rc.role_id for rc in role_configs if rc.ignored}
    return bool(ignored_ids.intersection(role_ids))
