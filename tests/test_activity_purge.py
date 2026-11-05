import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import hikari
import hikari.impl

from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.models import (
    ActivityGuildConfig,
    ActivityGuildMeta,
    LurkerPurge,
    PurgeCandidate,
)
from dragonpaw_bot.plugins.activity.purge import (
    BOOT_ID,
    SPARE_ID,
    build_purge_post,
    cancel_purge,
    next_action,
    purge_daily_step,
    select_candidates,
)

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


def _bot():
    return SimpleNamespace(
        rest=SimpleNamespace(
            build_message_action_row=hikari.impl.MessageActionRowBuilder,
            create_message=AsyncMock(return_value=SimpleNamespace(id=555)),
            edit_message=AsyncMock(),
        )
    )


def _gc():
    return SimpleNamespace(bot=_bot(), log_channel_id=7, log=AsyncMock())


def _many(n: int, spared: int = 0) -> LurkerPurge:
    return LurkerPurge(
        started_at=NOW,
        candidates=[
            PurgeCandidate(
                user_id=i,
                display_name=f"c{i}",
                spared_by="Staff" if i <= spared else None,
            )
            for i in range(1, n + 1)
        ],
    )


def _menu_options(components):
    menu = components[0].components[0]
    return menu, [o.value for o in menu.options]


def test_build_post_menu_lists_only_unspared():
    purge = _many(3, spared=1)
    embed, components = build_purge_post(_bot(), purge, 5)
    menu, values = _menu_options(components)
    assert values == ["2", "3"]
    assert menu.custom_id == SPARE_ID
    assert menu.max_values == 2
    assert "~~c1~~ — spared by Staff 💜" in embed.description


def test_build_post_menu_caps_at_25():
    _, components = build_purge_post(_bot(), _many(30), 5)
    _, values = _menu_options(components)
    assert values == [str(i) for i in range(1, 26)]
    _, components = build_purge_post(_bot(), _many(30, spared=5), 5)
    _, values = _menu_options(components)
    assert values == [str(i) for i in range(6, 31)]


def test_build_post_no_menu_when_all_spared():
    _, components = build_purge_post(_bot(), _many(2, spared=2), 5)
    assert components == []


def test_build_post_boot_button_only_after_final():
    purge = _many(2)
    _, components = build_purge_post(_bot(), purge, 5)
    assert len(components) == 1
    purge.boot_posted_at = NOW
    embed, components = build_purge_post(_bot(), purge, 5)
    button = components[-1].components[0]
    assert button.custom_id == BOOT_ID
    assert button.label == "Boot"
    assert "final" in embed.title


async def test_daily_step_start_posts_and_freezes_list(monkeypatch):
    monkeypatch.setattr(activity_state, "save_config", lambda meta: None)
    gc = _gc()
    meta = _meta()
    meta.config.lurker_role_id = ROLE
    await purge_daily_step(gc, meta, [_member("bob"), _member("Alice")], NOW)
    assert [c.display_name for c in meta.purge.candidates] == ["Alice", "bob"]
    assert meta.purge.message_id == 555
    assert meta.last_purge_started == NOW
    gc.bot.rest.create_message.assert_awaited_once()


async def test_daily_step_start_with_no_lurkers_sets_anchor_only(monkeypatch):
    monkeypatch.setattr(activity_state, "save_config", lambda meta: None)
    gc = _gc()
    meta = _meta()
    meta.config.lurker_role_id = ROLE
    await purge_daily_step(gc, meta, [_member("x", roles=())], NOW)
    assert meta.purge is None
    assert meta.last_purge_started == NOW
    gc.log.assert_awaited_once()
    gc.bot.rest.create_message.assert_not_awaited()


async def test_daily_step_remind_strips_previous_components(monkeypatch):
    monkeypatch.setattr(activity_state, "save_config", lambda meta: None)
    gc = _gc()
    meta = _meta(purge=_purge(message_id=123))
    await purge_daily_step(gc, meta, [], NOW)
    gc.bot.rest.edit_message.assert_awaited_once_with(7, 123, components=[])
    assert meta.purge.day == 2
    assert meta.purge.message_id == 555


async def test_daily_step_final_sets_boot_posted_at(monkeypatch):
    monkeypatch.setattr(activity_state, "save_config", lambda meta: None)
    gc = _gc()
    meta = _meta(purge=_purge(day=5, message_id=123))
    await purge_daily_step(gc, meta, [], NOW)
    assert meta.purge.boot_posted_at == NOW


async def test_daily_step_expire_clears_purge_and_posts_note(monkeypatch):
    monkeypatch.setattr(activity_state, "save_config", lambda meta: None)
    gc = _gc()
    posted = NOW - datetime.timedelta(days=14)
    meta = _meta(purge=_purge(day=5, boot_posted_at=posted))
    await purge_daily_step(gc, meta, [], NOW)
    assert meta.purge is None
    gc.log.assert_awaited_once()


async def test_list_frozen_new_lurker_not_added(monkeypatch):
    monkeypatch.setattr(activity_state, "save_config", lambda meta: None)
    gc = _gc()
    meta = _meta(purge=_purge())
    await purge_daily_step(gc, meta, [_member("a"), _member("b")], NOW)
    assert [c.user_id for c in meta.purge.candidates] == [1]


async def test_cancel_purge_clears_state():
    gc = _gc()
    meta = _meta(purge=_purge(message_id=123))
    await cancel_purge(gc, meta)
    assert meta.purge is None
    gc.bot.rest.edit_message.assert_awaited_once_with(7, 123, components=[])
    gc.log.assert_awaited_once()
