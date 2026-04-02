"""Tests for the activity tracker plugin — models, score calc, bucketing, pruning."""

import time
from types import SimpleNamespace

from dragonpaw_bot.plugins.activity import _add_contribution, _has_media
from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.cron import _prune_state
from dragonpaw_bot.plugins.activity.models import (
    ACTIVITY_FLOOR,
    PRUNE_DAYS,
    ActivityGuildConfig,
    ActivityGuildState,
    ContributionBucket,
    RoleConfig,
    UserActivity,
    best_role_config,
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
    assert has_ignored_role([99, 100], [staff]) is True


def test_has_ignored_role_false():
    normal = RoleConfig(role_id=99, role_name="Member", ignored=False)
    assert has_ignored_role([99], [normal]) is False


def test_has_ignored_role_empty_configs():
    assert has_ignored_role([1, 2, 3], []) is False


# ---------------------------------------------------------------------------- #
#                            _add_contribution                                 #
# ---------------------------------------------------------------------------- #


def test_add_contribution_new_user_creates_entry():
    st = ActivityGuildState(guild_id=1)
    _add_contribution(st, 42, "text", 1.0, now=1_000_000.0)
    assert 42 in st.users
    assert len(st.users[42].buckets) == 1
    assert st.users[42].buckets[0].amount == 1.0
    assert st.users[42].buckets[0].kind == "text"


def test_add_contribution_same_hour_same_kind_accumulates():
    st = ActivityGuildState(guild_id=1)
    _add_contribution(st, 42, "text", 1.0, now=1_000_000.0)
    _add_contribution(st, 42, "text", 2.0, now=1_000_000.0)
    assert len(st.users[42].buckets) == 1
    assert st.users[42].buckets[0].amount == 3.0


def test_add_contribution_different_kind_same_hour_separate_bucket():
    st = ActivityGuildState(guild_id=1)
    _add_contribution(st, 42, "text", 1.0, now=1_000_000.0)
    _add_contribution(st, 42, "media", 1.0, now=1_000_000.0)
    assert len(st.users[42].buckets) == 2


def test_add_contribution_different_hour_creates_new_bucket():
    st = ActivityGuildState(guild_id=1)
    _add_contribution(st, 42, "text", 1.0, now=1_000_000.0)
    _add_contribution(st, 42, "text", 1.0, now=1_000_000.0 + 3601)
    assert len(st.users[42].buckets) == 2


# ---------------------------------------------------------------------------- #
#                            _has_media                                        #
# ---------------------------------------------------------------------------- #


def _msg(content=None, attachments=None, stickers=None):
    return SimpleNamespace(
        content=content,
        attachments=attachments or [],
        stickers=stickers or [],
    )


def test_has_media_plain_text():
    assert _has_media(_msg(content="hello there")) is False


def test_has_media_with_attachment():
    assert _has_media(_msg(content="", attachments=[object()])) is True


def test_has_media_with_url_in_content():
    assert _has_media(_msg(content="check this https://example.com")) is True


def test_has_media_with_sticker():
    assert _has_media(_msg(content="", stickers=[object()])) is True


def test_has_media_none_content():
    assert _has_media(_msg(content=None)) is False


def test_has_media_http_url():
    assert _has_media(_msg(content="http://example.com")) is True


# ---------------------------------------------------------------------------- #
#                            _prune_state                                      #
# ---------------------------------------------------------------------------- #


def _make_st_with_user(user_id: int, days_ago_list: list[float]) -> ActivityGuildState:
    now = 1_000_000_000.0
    st = ActivityGuildState(guild_id=1)
    buckets = [
        ContributionBucket(
            hour=int(now - d * 86400) // 3600 * 3600,
            kind="text",
            amount=1.0,
        )
        for d in days_ago_list
    ]
    st.users[user_id] = UserActivity(user_id=user_id, buckets=buckets)
    return st, now


def test_prune_removes_old_buckets_keeps_recent():
    st, now = _make_st_with_user(1, [1, PRUNE_DAYS + 5])
    _prune_state(st, {1}, now)
    assert 1 in st.users
    assert len(st.users[1].buckets) == 1  # only the 1-day-old bucket survives


def test_prune_removes_user_with_no_buckets_left():
    st, now = _make_st_with_user(1, [PRUNE_DAYS + 5])  # only old bucket
    _prune_state(st, {1}, now)
    assert 1 not in st.users


def test_prune_removes_departed_member_with_recent_buckets():
    st, now = _make_st_with_user(1, [1])  # recent bucket, but user left
    _prune_state(st, set(), now)  # empty member_ids = user departed
    assert 1 not in st.users


def test_prune_keeps_active_present_member():
    st, now = _make_st_with_user(1, [1])
    _prune_state(st, {1}, now)
    assert 1 in st.users
    assert len(st.users[1].buckets) == 1


# ---------------------------------------------------------------------------- #
#                            State persistence                                 #
# ---------------------------------------------------------------------------- #


def test_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._cache.clear()

    st = ActivityGuildState(
        guild_id=1234,
        guild_name="Test Guild",
        config=ActivityGuildConfig(
            role_configs=[
                RoleConfig(
                    role_id=10,
                    role_name="Veteran",
                    contribution_multiplier=1.2,
                    decay_multiplier=1.5,
                )
            ],
            lurker_role_id=20,
            lurker_role_name="Lurker",
        ),
        users={
            42: UserActivity(
                user_id=42,
                buckets=[ContributionBucket(hour=1000000, kind="text", amount=3.0)],
            )
        },
    )
    activity_state.save(st)

    activity_state._cache.clear()
    loaded = activity_state.load(1234)

    assert loaded.guild_id == 1234
    assert loaded.guild_name == "Test Guild"
    assert len(loaded.config.role_configs) == 1
    assert loaded.config.role_configs[0].role_id == 10
    assert loaded.config.role_configs[0].contribution_multiplier == 1.2
    assert loaded.config.lurker_role_id == 20
    assert 42 in loaded.users
    assert loaded.users[42].buckets[0].amount == 3.0
    assert loaded.users[42].buckets[0].kind == "text"


def test_state_load_missing_returns_default(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._cache.clear()

    st = activity_state.load(9999)
    assert st.guild_id == 9999
    assert st.users == {}


def test_state_load_empty_yaml_returns_default(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._cache.clear()

    path = tmp_path / "activity_7777.yaml"
    path.write_text("")

    st = activity_state.load(7777)
    assert st.guild_id == 7777
    assert st.users == {}


def test_state_load_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._cache.clear()

    st = ActivityGuildState(guild_id=5555, guild_name="Cached")
    activity_state.save(st)

    first = activity_state.load(5555)
    second = activity_state.load(5555)
    assert first is second
