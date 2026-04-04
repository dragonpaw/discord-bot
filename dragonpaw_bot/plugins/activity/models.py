"""Activity tracker models and score calculation."""

from __future__ import annotations

import enum
import math
import time

import pydantic


class ContributionKind(enum.StrEnum):
    TEXT = "text"
    MEDIA = "media"
    REACTION = "reaction"
    VC = "vc"


CONTRIBUTION_VALUES: dict[ContributionKind, float] = {
    ContributionKind.TEXT: 1.0,
    ContributionKind.MEDIA: 2.0,
    ContributionKind.REACTION: 0.1,
    ContributionKind.VC: 0.1,  # per minute
}

BASE_HALF_LIFE = 14 * 24 * 3600  # 14 days in seconds
ACTIVITY_FLOOR = 0.3
PRUNE_THRESHOLD = ACTIVITY_FLOOR * 0.1  # 0.01 — bucket is negligible at 1% of floor
PRUNE_DAYS_MAX = 300  # hard cap; contribution-based pruning fires well before this


class ContributionBucket(pydantic.BaseModel):
    """Hourly aggregate of contributions of the same kind."""

    hour: int  # Unix timestamp floored to hour boundary
    kind: ContributionKind
    amount: float = pydantic.Field(gt=0)  # sum of contributions in this hour

    @pydantic.field_validator("hour")
    @classmethod
    def _hour_must_be_aligned(cls, v: int) -> int:
        if v % 3600 != 0:
            raise ValueError(f"hour must be aligned to 3600-second boundary, got {v}")
        return v


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
    point_multiplier: float = pydantic.Field(default=1.0, ge=0)


class ActivityGuildConfig(pydantic.BaseModel):
    role_configs: list[RoleConfig] = []
    channel_configs: list[ChannelConfig] = []
    lurker_role_id: int | None = None
    lurker_role_name: str = ""
    viewer_role_id: int | None = None
    viewer_role_name: str = ""


class ActivityGuildMeta(pydantic.BaseModel):
    """Persisted guild-level config and metadata (no user data)."""

    guild_id: int = pydantic.Field(gt=0)
    guild_name: str = ""
    config: ActivityGuildConfig = pydantic.Field(default_factory=ActivityGuildConfig)


class ActivityGuildState(pydantic.BaseModel):
    """Legacy combined model — used only for migrating old YAML files. TODO: remove after migration cycle."""

    guild_id: int = pydantic.Field(gt=0)
    guild_name: str = ""
    config: ActivityGuildConfig = pydantic.Field(default_factory=ActivityGuildConfig)
    users: dict[int, UserActivity] = {}


def bucket_is_negligible(
    bucket: ContributionBucket,
    now: float,
    half_life: float,
    contrib_mult: float,
) -> bool:
    """True when even at the best log_weight (position 0 = 1/ln(2)), the bucket's
    contribution is below PRUNE_THRESHOLD. Safe to delete without affecting scores."""
    t = now - bucket.hour
    base = CONTRIBUTION_VALUES.get(bucket.kind, 1.0) * bucket.amount
    max_contribution = (
        base * (1.0 / math.log(2)) * contrib_mult * math.pow(0.5, t / half_life)
    )
    return max_contribution < PRUNE_THRESHOLD


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
    by_kind: dict[ContributionKind, list[ContributionBucket]] = {}
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


def has_ignored_role(role_ids: list[int], role_configs: list[RoleConfig]) -> str | None:
    """Return the name of the first ignored role found, or None."""
    role_name_by_id = {rc.role_id: rc.role_name for rc in role_configs if rc.ignored}
    return next(
        (role_name_by_id[rid] for rid in role_ids if rid in role_name_by_id), None
    )
