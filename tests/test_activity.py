"""Tests for the activity tracker plugin — models, score calc, bucketing, pruning."""

import math
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import hikari
import pydantic
import pytest
import yaml

from dragonpaw_bot.plugins.activity import listeners as activity_listeners
from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.commands import (
    _EMBED_DESCRIPTION_LIMIT,
    _TRUNCATION_NOTE,
    _can_view_others,
    _classify_members,
    _truncate_description,
)
from dragonpaw_bot.plugins.activity.cron import (
    _evaluate_lurker,
    _prune_state,
    _sync_lurker_role,
)
from dragonpaw_bot.plugins.activity.listeners import (
    _add_contribution,
    _channel_multiplier,
)
from dragonpaw_bot.plugins.activity.models import (
    ACTIVITY_FLOOR,
    BASE_HALF_LIFE,
    PRUNE_THRESHOLD,
    ActivityGuildConfig,
    ActivityGuildMeta,
    ChannelConfig,
    ContributionBucket,
    ContributionKind,
    RoleConfig,
    UserActivity,
    best_role_config,
    bucket_is_negligible,
    calculate_score,
    has_ignored_role,
)

# ---------------------------------------------------------------------------- #
#                            calculate_score                                   #
# ---------------------------------------------------------------------------- #


def test_calculate_score_empty_buckets():
    assert calculate_score([], None) == 0.0


def test_calculate_score_single_recent_text_post():
    now = time.time()
    buckets = [
        ContributionBucket(hour=int(now) // 3600 * 3600, kind="text", amount=1.0)
    ]
    score = calculate_score(buckets, None, now=now)
    assert score > 0.0


def test_calculate_score_old_post_less_than_fresh():
    now = time.time()
    fresh_hour = int(now) // 3600 * 3600
    old_hour = fresh_hour - 30 * 24 * 3600  # 30 days ago

    fresh_buckets = [ContributionBucket(hour=fresh_hour, kind="text", amount=1.0)]
    old_buckets = [ContributionBucket(hour=old_hour, kind="text", amount=1.0)]

    fresh_score = calculate_score(fresh_buckets, None, now=now)
    old_score = calculate_score(old_buckets, None, now=now)

    assert fresh_score > old_score


def test_calculate_score_media_worth_more_than_text():
    now = time.time()
    hour = int(now) // 3600 * 3600

    text_score = calculate_score(
        [ContributionBucket(hour=hour, kind="text", amount=1.0)], None, now=now
    )
    media_score = calculate_score(
        [ContributionBucket(hour=hour, kind="media", amount=1.0)], None, now=now
    )

    assert media_score > text_score


def test_calculate_score_contribution_multiplier_applied():
    now = time.time()
    hour = int(now) // 3600 * 3600
    buckets = [ContributionBucket(hour=hour, kind="text", amount=1.0)]

    base_score = calculate_score(buckets, None, now=now)

    boosted_role = RoleConfig(
        role_id=1, role_name="Active", contribution_multiplier=1.1, decay_multiplier=1.0
    )
    boosted_score = calculate_score(buckets, boosted_role, now=now)

    assert boosted_score > base_score


def test_calculate_score_decay_multiplier_slows_decay():
    now = time.time()
    old_hour = int(now) // 3600 * 3600 - 7 * 24 * 3600  # 7 days ago

    buckets = [ContributionBucket(hour=old_hour, kind="text", amount=1.0)]
    base_score = calculate_score(buckets, None, now=now)

    slow_decay_role = RoleConfig(
        role_id=1,
        role_name="Veteran",
        contribution_multiplier=1.0,
        decay_multiplier=2.0,
    )
    slow_decay_score = calculate_score(buckets, slow_decay_role, now=now)

    assert slow_decay_score > base_score


def test_calculate_score_activity_bonus_extends_half_life():
    now = time.time()
    old_hour = int(now) // 3600 * 3600 - 7 * 24 * 3600

    single = [ContributionBucket(hour=old_hour, kind="text", amount=1.0)]
    single_score = calculate_score(single, None, now=now)

    # Many recent buckets boost the activity bonus, slowing decay for old bucket too
    recent = [
        ContributionBucket(
            hour=int(now) // 3600 * 3600 - i * 3600, kind="text", amount=1.0
        )
        for i in range(20)
    ]
    many_score = calculate_score(single + recent, None, now=now)
    assert many_score > single_score


def test_calculate_score_is_above_floor_when_recently_active():
    now = time.time()
    hour = int(now) // 3600 * 3600
    buckets = [ContributionBucket(hour=hour, kind="text", amount=1.0)]
    assert calculate_score(buckets, None, now=now) >= ACTIVITY_FLOOR


def test_calculate_score_very_old_post_below_floor():
    now = time.time()
    old_hour = int(now) // 3600 * 3600 - 200 * 24 * 3600  # 200 days ago
    buckets = [ContributionBucket(hour=old_hour, kind="text", amount=1.0)]
    assert calculate_score(buckets, None, now=now) < ACTIVITY_FLOOR


# ---------------------------------------------------------------------------- #
#                            best_role_config                                  #
# ---------------------------------------------------------------------------- #


def test_best_role_config_no_match():
    result = best_role_config([100, 200], [])
    assert result is None


def test_best_role_config_returns_highest_multiplier():
    role_a = RoleConfig(role_id=1, role_name="A", contribution_multiplier=1.0)
    role_b = RoleConfig(role_id=2, role_name="B", contribution_multiplier=1.2)
    result = best_role_config([1, 2], [role_a, role_b])
    assert result is role_b


def test_best_role_config_skips_ignored():
    ignored = RoleConfig(
        role_id=1, role_name="Staff", ignored=True, contribution_multiplier=1.5
    )
    normal = RoleConfig(role_id=2, role_name="Member", contribution_multiplier=1.0)
    result = best_role_config([1, 2], [ignored, normal])
    assert result is normal


def test_best_role_config_all_ignored_returns_none():
    ignored = RoleConfig(role_id=1, role_name="Staff", ignored=True)
    result = best_role_config([1], [ignored])
    assert result is None


# ---------------------------------------------------------------------------- #
#                            has_ignored_role                                  #
# ---------------------------------------------------------------------------- #


def test_has_ignored_role_true():
    staff = RoleConfig(role_id=99, role_name="Staff", ignored=True)
    assert has_ignored_role([99, 100], [staff]) == "Staff"


def test_has_ignored_role_false():
    normal = RoleConfig(role_id=99, role_name="Member", ignored=False)
    assert has_ignored_role([99], [normal]) is None


def test_has_ignored_role_empty_configs():
    assert has_ignored_role([1, 2, 3], []) is None


# ---------------------------------------------------------------------------- #
#                            _add_contribution                                 #
# ---------------------------------------------------------------------------- #


@pytest.fixture(autouse=False)
def clear_user_state():
    """Clear in-memory user caches before and after each test."""
    activity_state._user_cache.clear()
    activity_state._dirty_users.clear()
    yield
    activity_state._user_cache.clear()
    activity_state._dirty_users.clear()


def test_add_contribution_new_user_creates_entry(clear_user_state):
    _add_contribution(1, 42, "text", 1.0, now=1_000_000.0)
    ua = activity_state._user_cache.get((1, 42))
    assert ua is not None
    assert len(ua.buckets) == 1
    assert ua.buckets[0].amount == 1.0
    assert ua.buckets[0].kind == "text"
    assert (1, 42) in activity_state._dirty_users


def test_add_contribution_same_hour_same_kind_accumulates(clear_user_state):
    _add_contribution(1, 42, "text", 1.0, now=1_000_000.0)
    _add_contribution(1, 42, "text", 2.0, now=1_000_000.0)
    ua = activity_state._user_cache[(1, 42)]
    assert len(ua.buckets) == 1
    assert ua.buckets[0].amount == 3.0


def test_add_contribution_different_kind_same_hour_separate_bucket(clear_user_state):
    _add_contribution(1, 42, "text", 1.0, now=1_000_000.0)
    _add_contribution(1, 42, "media", 1.0, now=1_000_000.0)
    ua = activity_state._user_cache[(1, 42)]
    assert len(ua.buckets) == 2


def test_add_contribution_different_hour_creates_new_bucket(clear_user_state):
    _add_contribution(1, 42, "text", 1.0, now=1_000_000.0)
    _add_contribution(1, 42, "text", 1.0, now=1_000_000.0 + 3601)
    ua = activity_state._user_cache[(1, 42)]
    assert len(ua.buckets) == 2


# ---------------------------------------------------------------------------- #
#                            _prune_state                                      #
# ---------------------------------------------------------------------------- #


def _fake_member(user_id: int, role_ids: list[int] | None = None):
    """Minimal hikari.Member stand-in for prune tests."""
    return SimpleNamespace(role_ids=role_ids or [])


def _fake_bot():
    """Mock bot whose member lookups always miss (cache None, REST 404).

    Used by prune tests: present members are taken from the passed `members`
    dict, so the bot is only consulted for users absent from it — which must
    resolve as departed (None).
    """
    bot = MagicMock()
    bot.cache.get_member.return_value = None
    bot.rest.fetch_member = AsyncMock(
        side_effect=hikari.NotFoundError(url="", headers={}, raw_body=b"")
    )
    return bot


def _setup_prune(
    tmp_path,
    monkeypatch,
    user_id: int,
    days_ago_list: list[float],
    now: float = 1_000_000_000.0,
) -> tuple[ActivityGuildMeta, float]:
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._user_cache.clear()
    activity_state._config_cache.clear()

    meta = ActivityGuildMeta(guild_id=1)
    buckets = [
        ContributionBucket(
            hour=int(now - d * 86400) // 3600 * 3600,
            kind="text",
            amount=1.0,
        )
        for d in days_ago_list
    ]
    activity_state.save_user(1, user_id, UserActivity(user_id=user_id, buckets=buckets))
    return meta, now


async def test_prune_removes_negligible_buckets(tmp_path, monkeypatch):
    # Bucket 110 days old is well past the negligibility threshold (~100d for default config)
    meta, now = _setup_prune(tmp_path, monkeypatch, 1, [1, 110])
    await _prune_state(_fake_bot(), meta, {1: _fake_member(1)}, now)
    ua = activity_state.load_user(1, 1)
    assert ua is not None
    assert len(ua.buckets) == 1  # only the 1-day-old bucket survives


async def test_prune_removes_user_with_no_buckets_left(tmp_path, monkeypatch):
    meta, now = _setup_prune(tmp_path, monkeypatch, 1, [110])  # only negligible bucket
    await _prune_state(_fake_bot(), meta, {1: _fake_member(1)}, now)
    assert activity_state.load_user(1, 1) is None


async def test_prune_removes_departed_member(tmp_path, monkeypatch):
    meta, now = _setup_prune(tmp_path, monkeypatch, 1, [1])  # recent bucket, user left
    await _prune_state(_fake_bot(), meta, {}, now)  # empty members = departed
    assert activity_state.load_user(1, 1) is None


async def test_prune_keeps_active_present_member(tmp_path, monkeypatch):
    meta, now = _setup_prune(tmp_path, monkeypatch, 1, [1])
    await _prune_state(_fake_bot(), meta, {1: _fake_member(1)}, now)
    ua = activity_state.load_user(1, 1)
    assert ua is not None
    assert len(ua.buckets) == 1


async def test_prune_keeps_member_present_via_rest_when_cache_misses(
    tmp_path, monkeypatch
):
    # A partial cache can omit a present member; REST must confirm departure
    # before we delete their activity. Here the cache misses but REST says present.
    meta, now = _setup_prune(tmp_path, monkeypatch, 1, [1])
    bot = MagicMock()
    bot.cache.get_member.return_value = None
    bot.rest.fetch_member = AsyncMock(return_value=_fake_member(1))
    await _prune_state(bot, meta, {}, now)
    ua = activity_state.load_user(1, 1)
    assert ua is not None
    assert len(ua.buckets) == 1


# ---------------------------------------------------------------------------- #
#                            State persistence                                 #
# ---------------------------------------------------------------------------- #


def test_config_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._config_cache.clear()

    meta = ActivityGuildMeta(
        guild_id=1234,
        guild_name="Test Guild",
        config=ActivityGuildConfig(
            role_configs=[
                RoleConfig(
                    role_id=10,
                    role_name="Veteran",
                    contribution_multiplier=1.2,
                    decay_multiplier=1.7,
                )
            ],
            lurker_role_id=20,
            lurker_role_name="Lurker",
        ),
    )
    activity_state.save_config(meta)
    activity_state._config_cache.clear()

    loaded = activity_state.load_config(1234)
    assert loaded.guild_id == 1234
    assert loaded.guild_name == "Test Guild"
    assert len(loaded.config.role_configs) == 1
    assert loaded.config.role_configs[0].role_id == 10
    assert loaded.config.role_configs[0].contribution_multiplier == 1.2
    assert loaded.config.lurker_role_id == 20


def test_user_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._user_cache.clear()

    ua = UserActivity(
        user_id=42,
        buckets=[ContributionBucket(hour=3600000, kind="text", amount=3.0)],
    )
    activity_state.save_user(1234, 42, ua)
    activity_state._user_cache.clear()

    loaded = activity_state.load_user(1234, 42)
    assert loaded is not None
    assert loaded.user_id == 42
    assert loaded.buckets[0].amount == 3.0
    assert loaded.buckets[0].kind == "text"


def test_load_config_missing_returns_default(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._config_cache.clear()

    meta = activity_state.load_config(9999)
    assert meta.guild_id == 9999


def test_load_user_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._user_cache.clear()

    assert activity_state.load_user(9999, 42) is None


def test_load_config_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._config_cache.clear()

    meta = ActivityGuildMeta(guild_id=5555, guild_name="Cached")
    activity_state.save_config(meta)

    first = activity_state.load_config(5555)
    second = activity_state.load_config(5555)
    assert first is second


def test_migration_from_old_combined_file(tmp_path, monkeypatch):
    """Old activity_{guild_id}.yaml is split into config + per-user files on first load."""

    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._config_cache.clear()
    activity_state._user_cache.clear()

    old_data = {
        "guild_id": 7777,
        "guild_name": "Old Guild",
        "config": {
            "role_configs": [],
            "channel_configs": [],
            "lurker_role_id": None,
            "lurker_role_name": "",
            "viewer_role_id": None,
            "viewer_role_name": "",
        },
        "users": {
            99: {
                "user_id": 99,
                "buckets": [{"hour": 3600000, "kind": "text", "amount": 2.0}],
            }
        },
    }
    old_path = tmp_path / "activity_7777.yaml"
    with open(old_path, "w") as f:
        yaml.dump(old_data, f)

    meta = activity_state.load_config(7777)
    assert meta.guild_id == 7777
    assert meta.guild_name == "Old Guild"

    ua = activity_state.load_user(7777, 99)
    assert ua is not None
    assert ua.buckets[0].amount == 2.0

    # Old file should be gone
    assert not old_path.exists()
    assert (tmp_path / "activity_config_7777.yaml").exists()
    assert (tmp_path / "activity_user_7777_99.yaml").exists()


def test_list_user_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._user_cache.clear()

    activity_state.save_user(1, 10, UserActivity(user_id=10, buckets=[]))
    activity_state.save_user(1, 20, UserActivity(user_id=20, buckets=[]))
    activity_state.save_user(
        2, 10, UserActivity(user_id=10, buckets=[])
    )  # different guild

    ids = activity_state.list_user_ids(1)
    assert sorted(ids) == [10, 20]


def test_delete_user(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._user_cache.clear()

    ua = UserActivity(user_id=42, buckets=[])
    activity_state.save_user(1, 42, ua)
    assert activity_state.load_user(1, 42) is not None

    activity_state.delete_user(1, 42)
    activity_state._user_cache.clear()
    assert activity_state.load_user(1, 42) is None


# ---------------------------------------------------------------------------- #
#                            ContributionBucket validation                     #
# ---------------------------------------------------------------------------- #


def test_contribution_bucket_invalid_kind():
    with pytest.raises(pydantic.ValidationError):
        ContributionBucket(hour=3600, kind="invalid", amount=1.0)


def test_contribution_bucket_unaligned_hour():
    with pytest.raises(pydantic.ValidationError):
        ContributionBucket(hour=3601, kind="text", amount=1.0)


def test_contribution_bucket_aligned_hour_ok():
    b = ContributionBucket(hour=3600, kind="text", amount=1.0)
    assert b.hour == 3600


def test_channel_config_zero_multiplier_allowed():
    cc = ChannelConfig(channel_id=1, channel_name="silent", point_multiplier=0.0)
    assert cc.point_multiplier == 0.0


# ---------------------------------------------------------------------------- #
#                            bucket_is_negligible                              #
# ---------------------------------------------------------------------------- #


def test_bucket_is_negligible_recent_not_negligible():
    now = 1_000_000_000.0
    hour = int(now) // 3600 * 3600
    b = ContributionBucket(hour=hour, kind="text", amount=1.0)
    assert not bucket_is_negligible(b, now, BASE_HALF_LIFE, 1.0)


def test_bucket_is_negligible_old_is_negligible():
    now = 1_000_000_000.0
    old_hour = int(now - 110 * 24 * 3600) // 3600 * 3600
    b = ContributionBucket(hour=old_hour, kind="text", amount=1.0)
    assert bucket_is_negligible(b, now, BASE_HALF_LIFE, 1.0)


def test_bucket_is_negligible_longer_half_life_delays_pruning():
    now = 1_000_000_000.0
    old_hour = int(now - 110 * 24 * 3600) // 3600 * 3600
    b = ContributionBucket(hour=old_hour, kind="text", amount=1.0)
    assert bucket_is_negligible(b, now, BASE_HALF_LIFE, 1.0)
    assert not bucket_is_negligible(b, now, BASE_HALF_LIFE * 2, 1.0)


def test_bucket_is_negligible_uses_prune_threshold():
    """Max contribution just above PRUNE_THRESHOLD is not negligible."""

    now = 1_000_000_000.0
    half_life = BASE_HALF_LIFE
    # t_cross: age at which max contribution = PRUNE_THRESHOLD exactly
    # max = 1/ln(2) * 0.5^(t/hl) = PRUNE_THRESHOLD
    t_cross = half_life * math.log2(1.0 / (PRUNE_THRESHOLD * math.log(2)))
    # Slightly newer than threshold → not negligible
    newer_hour = int(now - t_cross * 0.98) // 3600 * 3600
    assert not bucket_is_negligible(
        ContributionBucket(hour=newer_hour, kind="text", amount=1.0),
        now,
        half_life,
        1.0,
    )
    # Slightly older than threshold → negligible
    older_hour = int(now - t_cross * 1.02) // 3600 * 3600
    assert bucket_is_negligible(
        ContributionBucket(hour=older_hour, kind="text", amount=1.0),
        now,
        half_life,
        1.0,
    )


# ---------------------------------------------------------------------------- #
#                            _prune_state return value                         #
# ---------------------------------------------------------------------------- #


async def test_prune_returns_surviving_bucket_count(tmp_path, monkeypatch):
    meta, now = _setup_prune(tmp_path, monkeypatch, 1, [1, 2])
    count = await _prune_state(_fake_bot(), meta, {1: _fake_member(1)}, now)
    assert count == 2


async def test_prune_returns_zero_when_all_pruned(tmp_path, monkeypatch):
    meta, now = _setup_prune(tmp_path, monkeypatch, 1, [110])
    count = await _prune_state(_fake_bot(), meta, {1: _fake_member(1)}, now)
    assert count == 0


async def test_prune_departed_user_not_counted(tmp_path, monkeypatch):
    meta, now = _setup_prune(tmp_path, monkeypatch, 1, [1])
    count = await _prune_state(_fake_bot(), meta, {}, now)
    assert count == 0


async def test_prune_cleans_up_empty_user_file(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._user_cache.clear()
    activity_state._config_cache.clear()

    (tmp_path / "activity_user_1_77.yaml").write_text("")

    meta = ActivityGuildMeta(guild_id=1)
    await _prune_state(_fake_bot(), meta, {77: _fake_member(77)}, 1_000_000_000.0)

    activity_state._user_cache.clear()
    assert activity_state.load_user(1, 77) is None
    assert 77 not in activity_state.list_user_ids(1)


# ---------------------------------------------------------------------------- #
#                            list_user_ids robustness                          #
# ---------------------------------------------------------------------------- #


def test_list_user_ids_ignores_malformed_files(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._user_cache.clear()

    activity_state.save_user(1, 10, UserActivity(user_id=10, buckets=[]))
    (tmp_path / "activity_user_1_notanint.yaml").write_text("")

    ids = activity_state.list_user_ids(1)
    assert ids == [10]


# ---------------------------------------------------------------------------- #
#                            flush_dirty                                       #
# ---------------------------------------------------------------------------- #


def test_flush_dirty_returns_count(tmp_path, monkeypatch, clear_user_state):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)

    _add_contribution(1, 42, "text", 1.0, now=1_000_000_000.0)
    _add_contribution(1, 43, "text", 1.0, now=1_000_000_000.0)
    count = activity_state.flush_dirty()
    assert count == 2


def test_flush_dirty_missing_cache_entry_cleaned_up(
    tmp_path, monkeypatch, clear_user_state
):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)

    activity_state._dirty_users.add((1, 999))  # dirty but no cache entry

    count = activity_state.flush_dirty()

    assert count == 0
    assert (1, 999) not in activity_state._dirty_users


# ---------------------------------------------------------------------------- #
#                            migration                                         #
# ---------------------------------------------------------------------------- #


def test_migration_empty_old_file(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._config_cache.clear()
    activity_state._user_cache.clear()

    old_path = tmp_path / "activity_8888.yaml"
    old_path.write_text("")

    meta = activity_state.load_config(8888)
    assert meta.guild_id == 8888
    assert not old_path.exists()


# ---------------------------------------------------------------------------- #
#                            _classify_members                                 #
# ---------------------------------------------------------------------------- #


def _fake_classify_member(user_id: int, role_ids=None, is_bot=False):
    return SimpleNamespace(is_bot=is_bot, role_ids=role_ids or [], id=user_id)


def test_classify_members_owner_is_immune(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._user_cache.clear()
    activity_state._config_cache.clear()

    meta = ActivityGuildMeta(guild_id=1)
    now = 1_000_000_000.0
    owner = _fake_classify_member(99)
    other = _fake_classify_member(42)

    immune, scored = _classify_members({99: owner, 42: other}, meta, now, owner_id=99)

    assert len(immune) == 1
    assert immune[0][0] is owner
    assert immune[0][1] == "Guild Owner"
    assert len(scored) == 1
    assert scored[0][1] is other


def test_classify_members_no_owner_id_owner_is_scored(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._user_cache.clear()
    activity_state._config_cache.clear()

    meta = ActivityGuildMeta(guild_id=1)
    owner = _fake_classify_member(99)

    immune, scored = _classify_members(
        {99: owner}, meta, 1_000_000_000.0, owner_id=None
    )

    assert len(immune) == 0
    assert len(scored) == 1


def test_classify_members_immune_member_has_score(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._user_cache.clear()
    activity_state._config_cache.clear()

    now = 1_000_000_000.0
    meta = ActivityGuildMeta(
        guild_id=1,
        config=ActivityGuildConfig(
            role_configs=[RoleConfig(role_id=5, role_name="Staff", ignored=True)]
        ),
    )
    hour = int(now) // 3600 * 3600
    activity_state.save_user(
        1,
        50,
        UserActivity(
            user_id=50, buckets=[ContributionBucket(hour=hour, kind="text", amount=1.0)]
        ),
    )

    member = _fake_classify_member(50, role_ids=[5])
    immune, _ = _classify_members({50: member}, meta, now, owner_id=None)

    assert len(immune) == 1
    _, role_name, score = immune[0]
    assert role_name == "Staff"
    assert score > 0.0


# ---------------------------------------------------------------------------- #
#                            _sync_lurker_role — owner skip                   #
# ---------------------------------------------------------------------------- #


async def test_sync_lurker_role_skips_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._user_cache.clear()
    activity_state._config_cache.clear()

    lurker_role_id = 999
    owner_id = 88
    now = 1_000_000_000.0

    meta = ActivityGuildMeta(
        guild_id=1,
        config=ActivityGuildConfig(
            lurker_role_id=lurker_role_id, lurker_role_name="Lurker"
        ),
    )

    owner = SimpleNamespace(
        is_bot=False,
        id=owner_id,
        role_ids=[],
        display_name="Owner",
        mention="<@88>",
    )

    bot_mock = MagicMock()
    bot_mock.rest.add_role_to_member = AsyncMock()
    bot_mock.rest.remove_role_from_member = AsyncMock()

    gc = MagicMock()
    gc.bot = bot_mock
    gc.guild_id = 1
    gc.name = "Test Guild"
    gc.log = AsyncMock()

    await _sync_lurker_role(gc, meta, {owner_id: owner}, now, owner_id)

    bot_mock.rest.add_role_to_member.assert_not_called()
    bot_mock.rest.remove_role_from_member.assert_not_called()


# ---------------------------------------------------------------------------- #
#                            _evaluate_lurker                                  #
# ---------------------------------------------------------------------------- #


def _meta_with_ignored_role(role_id: int = 7) -> ActivityGuildMeta:
    return ActivityGuildMeta(
        guild_id=1,
        config=ActivityGuildConfig(
            role_configs=[RoleConfig(role_id=role_id, role_name="Staff", ignored=True)]
        ),
    )


def test_evaluate_lurker_immunity_beats_low_score():
    """Immune members must not be lurkered even when their activity is below the floor."""
    meta = _meta_with_ignored_role()
    should_lurker, reason = _evaluate_lurker(role_ids=[7], score=0.0, meta=meta)
    assert should_lurker is False
    assert reason == "gained immunity"


def test_evaluate_lurker_low_score_no_longer_active():
    """A non-immune member below the activity floor is lurkered."""
    meta = ActivityGuildMeta(guild_id=1)
    should_lurker, reason = _evaluate_lurker(
        role_ids=[1], score=ACTIVITY_FLOOR - 0.01, meta=meta
    )
    assert should_lurker is True
    assert reason == "no longer active"


def test_evaluate_lurker_active_default():
    """Active member is not a lurker."""
    meta = ActivityGuildMeta(guild_id=1)
    should_lurker, reason = _evaluate_lurker(role_ids=[1], score=1.0, meta=meta)
    assert should_lurker is False
    assert reason == "now active"


def test_evaluate_lurker_score_at_floor_is_not_lurker():
    """Boundary: score == ACTIVITY_FLOOR is not a lurker (the check uses `<`, not `<=`)."""
    meta = ActivityGuildMeta(guild_id=1)
    should_lurker, reason = _evaluate_lurker(
        role_ids=[1], score=ACTIVITY_FLOOR, meta=meta
    )
    assert should_lurker is False
    assert reason == "now active"


async def test_forwarded_media_message_scores_as_media(monkeypatch):
    """A forwarded image carries its media in message_snapshots — it must count
    as MEDIA (2.0), not TEXT, same as media_channels' detection."""
    snap = SimpleNamespace(content=None, attachments=[object()], stickers=[])
    msg = SimpleNamespace(
        content=None, attachments=[], stickers=[], message_snapshots=[snap]
    )
    msg.author = SimpleNamespace(is_bot=False)

    event = MagicMock()
    event.message = msg
    event.guild_id = hikari.Snowflake(1)
    event.author_id = hikari.Snowflake(42)
    event.channel_id = hikari.Snowflake(7)

    meta = SimpleNamespace(
        guild_name="G", config=SimpleNamespace(channel_configs=[])
    )
    monkeypatch.setattr(activity_listeners.activity_state, "load_config", lambda _g: meta)
    monkeypatch.setattr(
        activity_listeners,
        "guild_member",
        AsyncMock(return_value=SimpleNamespace(role_ids=[hikari.Snowflake(5)])),
    )
    recorded: list[tuple] = []
    monkeypatch.setattr(
        activity_listeners,
        "_add_contribution",
        lambda gid, uid, kind, amount, now=None: recorded.append((kind, amount)),
    )

    await activity_listeners._handle_message(event)

    assert recorded == [(ContributionKind.MEDIA, 1.0)]


# ---------------------------------------------------------------------------- #
#                       viewer permission rule                                 #
# ---------------------------------------------------------------------------- #


def _viewer_ctx(permissions, role_ids=()):
    ctx = MagicMock()
    ctx.guild_id = hikari.Snowflake(1)
    ctx.member.permissions = permissions
    ctx.member.role_ids = [hikari.Snowflake(r) for r in role_ids]
    return ctx


def _viewer_meta(monkeypatch, viewer_role_id):
    meta = SimpleNamespace(config=SimpleNamespace(viewer_role_id=viewer_role_id))
    monkeypatch.setattr(activity_state, "load_config", lambda _g: meta)


def test_can_view_others_admin(monkeypatch):
    _viewer_meta(monkeypatch, viewer_role_id=None)
    ctx = _viewer_ctx(hikari.Permissions.MANAGE_GUILD)
    assert _can_view_others(ctx) is True


def test_can_view_others_viewer_role(monkeypatch):
    _viewer_meta(monkeypatch, viewer_role_id=55)
    ctx = _viewer_ctx(hikari.Permissions.NONE, role_ids=[55])
    assert _can_view_others(ctx) is True


def test_can_view_others_plain_member(monkeypatch):
    _viewer_meta(monkeypatch, viewer_role_id=55)
    ctx = _viewer_ctx(hikari.Permissions.NONE, role_ids=[7])
    assert _can_view_others(ctx) is False


def test_channel_multiplier_configured():
    meta = SimpleNamespace(
        config=SimpleNamespace(
            channel_configs=[SimpleNamespace(channel_id=7, point_multiplier=2.5)]
        )
    )
    assert _channel_multiplier(meta, 7) == 2.5


def test_channel_multiplier_unconfigured_defaults_to_one():
    meta = SimpleNamespace(config=SimpleNamespace(channel_configs=[]))
    assert _channel_multiplier(meta, 7) == 1.0


# ---------------------------------------------------------------------------- #
#                      voice-state session accounting                          #
# ---------------------------------------------------------------------------- #


@pytest.fixture(autouse=False)
def clear_vc_sessions():
    """Clear the module-level VC session map before and after each test."""
    activity_listeners._vc_sessions.clear()
    yield
    activity_listeners._vc_sessions.clear()


def _vc_event(guild_id, user_id, old_channel, new_channel):
    """A VoiceStateUpdateEvent stand-in; channel None means "not in a channel"."""
    return SimpleNamespace(
        app=MagicMock(),
        guild_id=hikari.Snowflake(guild_id),
        old_state=(
            None
            if old_channel is None
            else SimpleNamespace(channel_id=hikari.Snowflake(old_channel))
        ),
        state=SimpleNamespace(
            user_id=hikari.Snowflake(user_id),
            channel_id=(None if new_channel is None else hikari.Snowflake(new_channel)),
        ),
    )


def _vc_setup(monkeypatch, channel_mults=(), *, is_bot=False, role_ids=(5,)):
    """Patch the listener's clock, member lookup, config and contribution sink.

    Returns (recorded, clock): `recorded` collects _add_contribution calls,
    `clock[0]` is the time the listener sees.
    """
    clock = [0.0]
    monkeypatch.setattr(
        activity_listeners, "time", SimpleNamespace(time=lambda: clock[0])
    )
    meta = SimpleNamespace(
        guild_name="G",
        config=SimpleNamespace(
            channel_configs=[
                SimpleNamespace(channel_id=cid, point_multiplier=mult)
                for cid, mult in channel_mults
            ]
        ),
    )
    monkeypatch.setattr(
        activity_listeners.activity_state, "load_config", lambda _g: meta
    )
    monkeypatch.setattr(
        activity_listeners,
        "guild_member",
        AsyncMock(
            return_value=SimpleNamespace(
                is_bot=is_bot, role_ids=[hikari.Snowflake(r) for r in role_ids]
            )
        ),
    )
    recorded: list[tuple] = []
    monkeypatch.setattr(
        activity_listeners,
        "_add_contribution",
        lambda gid, uid, kind, amount, now=None: recorded.append(
            (gid, uid, kind, amount)
        ),
    )
    return recorded, clock


async def test_vc_join_stores_session_timestamp(monkeypatch, clear_vc_sessions):
    """Fails if the join branch stops seeding _vc_sessions with the current time."""
    recorded, clock = _vc_setup(monkeypatch)
    clock[0] = 1_000.0

    await activity_listeners._handle_voice_state_update(_vc_event(1, 42, None, 7))

    assert activity_listeners._vc_sessions[1][42] == 1_000.0
    assert recorded == []


async def test_vc_leave_records_minutes_times_channel_multiplier(
    monkeypatch, clear_vc_sessions
):
    """Fails if elapsed time stops being converted to minutes, or the leaving
    channel's multiplier stops being applied."""
    recorded, clock = _vc_setup(monkeypatch, channel_mults=[(7, 2.5)])
    clock[0] = 1_000.0
    await activity_listeners._handle_voice_state_update(_vc_event(1, 42, None, 7))

    clock[0] = 1_000.0 + 120.0  # 2 minutes in the channel
    await activity_listeners._handle_voice_state_update(_vc_event(1, 42, 7, None))

    assert recorded == [(1, 42, ContributionKind.VC, 5.0)]  # 2 min x 2.5
    assert activity_listeners._vc_sessions[1] == {}


async def test_vc_leave_under_one_minute_records_nothing(
    monkeypatch, clear_vc_sessions
):
    """Fails if the `minutes >= 1.0` floor is dropped — a drive-by join/leave
    would then score."""
    recorded, clock = _vc_setup(monkeypatch, channel_mults=[(7, 2.5)])
    clock[0] = 1_000.0
    await activity_listeners._handle_voice_state_update(_vc_event(1, 42, None, 7))

    clock[0] = 1_000.0 + 59.0
    await activity_listeners._handle_voice_state_update(_vc_event(1, 42, 7, None))

    assert recorded == []
    assert activity_listeners._vc_sessions[1] == {}


async def test_vc_channel_switch_scores_old_channel_and_restarts_session(
    monkeypatch, clear_vc_sessions
):
    """Fails if a switch stops closing the old session (double-counting on the
    eventual leave) or scores with the destination channel's multiplier."""
    recorded, clock = _vc_setup(monkeypatch, channel_mults=[(7, 2.0), (8, 10.0)])
    clock[0] = 500.0
    await activity_listeners._handle_voice_state_update(_vc_event(1, 42, None, 7))

    clock[0] = 500.0 + 180.0  # 3 minutes in channel 7, then move to channel 8
    await activity_listeners._handle_voice_state_update(_vc_event(1, 42, 7, 8))

    assert recorded == [(1, 42, ContributionKind.VC, 6.0)]  # 3 min x 2.0
    assert activity_listeners._vc_sessions[1][42] == 680.0


async def test_vc_leave_without_recorded_join_records_nothing(
    monkeypatch, clear_vc_sessions
):
    """Fails if a missing session start stops being tolerated — a leave after a
    bot restart would score from a bogus baseline (or raise)."""
    recorded, clock = _vc_setup(monkeypatch, channel_mults=[(7, 2.0)])
    clock[0] = 1_000.0

    await activity_listeners._handle_voice_state_update(_vc_event(1, 42, 7, None))

    assert recorded == []
    assert activity_listeners._vc_sessions == {}


async def test_vc_leave_by_bot_records_nothing(monkeypatch, clear_vc_sessions):
    """Fails if the is_bot guard is dropped — music bots parked in VC would
    out-score every human."""
    recorded, clock = _vc_setup(monkeypatch, channel_mults=[(7, 2.0)], is_bot=True)
    clock[0] = 1_000.0
    await activity_listeners._handle_voice_state_update(_vc_event(1, 42, None, 7))

    clock[0] = 1_000.0 + 600.0
    await activity_listeners._handle_voice_state_update(_vc_event(1, 42, 7, None))

    assert recorded == []


async def test_vc_leave_from_zero_multiplier_channel_records_nothing(
    monkeypatch, clear_vc_sessions
):
    """Fails if a 0 multiplier stops silencing VC time — an AFK channel would
    then score like any other."""
    recorded, clock = _vc_setup(monkeypatch, channel_mults=[(7, 0.0)])
    clock[0] = 1_000.0
    await activity_listeners._handle_voice_state_update(_vc_event(1, 42, None, 7))

    clock[0] = 1_000.0 + 600.0
    await activity_listeners._handle_voice_state_update(_vc_event(1, 42, 7, None))

    assert recorded == []


# ---------------------------------------------------------------------------- #
#                        _truncate_description                                 #
# ---------------------------------------------------------------------------- #


def test_truncate_description_within_budget_unchanged():
    """Fails if short reports gain a truncation note or lose their joining."""
    lines = ["🐉 Amber — 4.20", "💤 Basil — 0.01"]
    assert _truncate_description(lines) == "🐉 Amber — 4.20\n💤 Basil — 0.01"


def test_truncate_description_over_budget_keeps_a_prefix_and_notes_it():
    """Fails if truncation stops keeping whole leading lines in order, or the
    result stops fitting inside Discord's description limit."""
    lines = [f"🐉 member {i:04d} " + "z" * 80 for i in range(100)]

    result = _truncate_description(lines)

    assert result.endswith(_TRUNCATION_NOTE)
    kept = result.removesuffix(_TRUNCATION_NOTE).split("\n")
    assert 0 < len(kept) < len(lines)
    assert kept == lines[: len(kept)]
    assert len(result) <= _EMBED_DESCRIPTION_LIMIT


def test_truncate_description_charges_for_newline_separators():
    """Packs single-character lines so the '+1 for the newline' accounting is
    the only thing keeping the result inside the limit: fails if that +1 is
    dropped (result overflows) and if the budget check turns pessimistic
    (result stops being packed tight)."""
    result = _truncate_description(["x"] * 5000)

    assert result.endswith(_TRUNCATION_NOTE)
    assert _EMBED_DESCRIPTION_LIMIT - 1 <= len(result) <= _EMBED_DESCRIPTION_LIMIT


def test_truncate_description_single_oversized_line_keeps_nothing():
    """The empty-`kept` branch: fails if it emits a stray leading newline or
    returns the oversized line anyway."""
    assert _truncate_description(["y" * (_EMBED_DESCRIPTION_LIMIT + 1)]) == (
        _TRUNCATION_NOTE
    )
