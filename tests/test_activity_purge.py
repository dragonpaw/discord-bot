import datetime
from types import SimpleNamespace

from dragonpaw_bot.plugins.activity.models import (
    ActivityGuildConfig,
    ActivityGuildMeta,
    LurkerPurge,
    PurgeCandidate,
)
from dragonpaw_bot.plugins.activity.purge import next_action, select_candidates

NOW = datetime.datetime(2026, 11, 4, 12, 0, tzinfo=datetime.UTC)
ROLE = 99


def _member(name: str, *, roles: tuple[int, ...] = (ROLE,), bot: bool = False):
    return SimpleNamespace(
        id=hash(name) % 10_000, display_name=name, role_ids=roles, is_bot=bot
    )


def _meta(*, enabled: bool = True, **kwargs) -> ActivityGuildMeta:
    return ActivityGuildMeta(
        guild_id=1, config=ActivityGuildConfig(purge_enabled=enabled), **kwargs
    )


def _purge(**kwargs) -> LurkerPurge:
    return LurkerPurge(
        started_at=NOW,
        candidates=[PurgeCandidate(user_id=1, display_name="a")],
        **kwargs,
    )


def test_select_candidates_only_role_holders_sorted():
    members = [
        _member("bob"),
        _member("Alice"),
        _member("carol", roles=()),
        _member("robot", bot=True),
    ]
    assert [c.display_name for c in select_candidates(members, ROLE)] == [
        "Alice",
        "bob",
    ]


def test_next_action_disabled_is_none():
    assert next_action(_meta(enabled=False), NOW) is None


def test_next_action_first_start_when_never_run():
    assert next_action(_meta(), NOW) == "start"


def test_next_action_waits_until_interval():
    three_ago = datetime.datetime(2026, 8, 4, 12, 0, tzinfo=datetime.UTC)
    four_ago = datetime.datetime(2026, 7, 4, 12, 0, tzinfo=datetime.UTC)
    assert next_action(_meta(last_purge_started=three_ago), NOW) is None
    assert next_action(_meta(last_purge_started=four_ago), NOW) == "start"


def test_next_action_remind_then_final():
    assert next_action(_meta(purge=_purge(day=1)), NOW) == "remind"
    assert next_action(_meta(purge=_purge(day=4)), NOW) == "remind"
    assert next_action(_meta(purge=_purge(day=5)), NOW) == "final"


def test_next_action_expire_after_14_days():
    def boot(days: int) -> ActivityGuildMeta:
        posted = NOW - datetime.timedelta(days=days)
        return _meta(purge=_purge(day=6, boot_posted_at=posted))

    assert next_action(boot(13), NOW) is None
    assert next_action(boot(14), NOW) == "expire"


def test_meta_round_trips_with_purge():
    purge = LurkerPurge(
        started_at=NOW,
        candidates=[PurgeCandidate(user_id=1, display_name="a", spared_by="Staff")],
    )
    meta = _meta(purge=purge, last_purge_started=NOW)
    again = ActivityGuildMeta.model_validate(meta.model_dump(mode="json"))
    assert again == meta
