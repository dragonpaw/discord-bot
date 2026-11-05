import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import hikari
import hikari.impl

from dragonpaw_bot import journal
from dragonpaw_bot.plugins.activity import config as activity_config
from dragonpaw_bot.plugins.activity import purge as purge_module
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
    handle_boot,
    handle_spare,
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
    meta = _meta(purge=_purge(day=5, boot_posted_at=posted, message_id=123))
    await purge_daily_step(gc, meta, [], NOW)
    assert meta.purge is None
    gc.bot.rest.edit_message.assert_awaited_once_with(7, 123, components=[])
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


def test_build_post_caps_description_at_4096():
    purge = _many(400)
    for c in purge.candidates:
        c.display_name = "x" * 30
    embed, _ = build_purge_post(_bot(), purge, 5)
    assert len(embed.description) <= 4096
    assert embed.description.endswith(" more")
    assert "…and " in embed.description


OWNER = 500
STAFF_ROLE = 77


def _spare_setup(monkeypatch, *, purge=None, roles=(STAFF_ROLE,), message_id=555):
    meta = ActivityGuildMeta(
        guild_id=1,
        config=ActivityGuildConfig(purge_staff_role_id=STAFF_ROLE),
        purge=purge,
    )
    monkeypatch.setattr(activity_state, "load_config", lambda _gid: meta)
    monkeypatch.setattr(activity_state, "save_config", lambda _m: None)
    record = MagicMock()
    monkeypatch.setattr(journal, "record", record)
    interaction = SimpleNamespace(
        guild_id=1,
        app=_bot(),
        member=SimpleNamespace(id=9, display_name="Staffer", role_ids=roles),
        message=SimpleNamespace(id=message_id),
        values=["1"],
        get_guild=lambda: SimpleNamespace(owner_id=OWNER),
        create_initial_response=AsyncMock(),
    )
    return meta, interaction, record


def _response_type(interaction):
    return interaction.create_initial_response.await_args.args[0]


async def test_spare_denied_for_non_staff(monkeypatch):
    purge = _many(2)
    purge.message_id = 555
    _, interaction, record = _spare_setup(monkeypatch, purge=purge, roles=())
    await handle_spare(interaction)
    assert _response_type(interaction) == hikari.ResponseType.MESSAGE_CREATE
    assert purge.candidates[0].spared_by is None
    record.assert_not_called()


async def test_spare_marks_and_updates_message(monkeypatch):
    purge = _many(2)
    purge.message_id = 555
    _, interaction, _ = _spare_setup(monkeypatch, purge=purge)
    await handle_spare(interaction)
    assert _response_type(interaction) == hikari.ResponseType.MESSAGE_UPDATE
    assert purge.candidates[0].spared_by == "Staffer"
    components = interaction.create_initial_response.await_args.kwargs["components"]
    assert _menu_options(components)[1] == ["2"]


async def test_spare_is_one_way(monkeypatch):
    purge = _many(2, spared=1)
    purge.message_id = 555
    _, interaction, record = _spare_setup(monkeypatch, purge=purge)
    await handle_spare(interaction)
    assert purge.candidates[0].spared_by == "Staff"
    record.assert_not_called()


async def test_spare_rejected_after_purge_cleared(monkeypatch):
    _, interaction, record = _spare_setup(monkeypatch, purge=None)
    await handle_spare(interaction)
    assert _response_type(interaction) == hikari.ResponseType.MESSAGE_CREATE
    record.assert_not_called()


async def test_spare_rejected_on_old_message(monkeypatch):
    purge = _many(2)
    purge.message_id = 556
    _, interaction, record = _spare_setup(monkeypatch, purge=purge)
    await handle_spare(interaction)
    assert _response_type(interaction) == hikari.ResponseType.MESSAGE_CREATE
    assert purge.candidates[0].spared_by is None
    record.assert_not_called()


async def test_spare_writes_journal_entry(monkeypatch):
    purge = _many(2)
    purge.message_id = 555
    _, interaction, record = _spare_setup(monkeypatch, purge=purge)
    await handle_spare(interaction)
    kwargs = record.call_args.kwargs
    assert kwargs["kind"] == "lurker_spared"
    assert kwargs["user_id"] == 1
    assert "Staffer" in kwargs["summary"]


async def test_spare_allowed_on_boot_post(monkeypatch):
    purge = _many(2)
    purge.message_id = 555
    purge.boot_posted_at = NOW
    _, interaction, _ = _spare_setup(monkeypatch, purge=purge)
    await handle_spare(interaction)
    assert _response_type(interaction) == hikari.ResponseType.MESSAGE_UPDATE
    assert purge.candidates[0].spared_by == "Staffer"


async def test_spare_allowed_for_owner(monkeypatch):
    purge = _many(2)
    purge.message_id = 555
    _, interaction, _ = _spare_setup(monkeypatch, purge=purge, roles=())
    interaction.member.id = OWNER
    await handle_spare(interaction)
    assert _response_type(interaction) == hikari.ResponseType.MESSAGE_UPDATE


def _boot_setup(monkeypatch, *, purge, roles=(STAFF_ROLE,), departed=(), calls=None):
    meta, interaction, record = _spare_setup(monkeypatch, purge=purge, roles=roles)
    interaction.get_guild = lambda: SimpleNamespace(owner_id=OWNER, name="Den")
    calls = [] if calls is None else calls

    async def kick_user(_guild_id, user_id, *, reason):
        calls.append(("kick", user_id))

    interaction.app.rest.kick_user = AsyncMock(side_effect=kick_user)

    async def guild_member(_bot, _guild_id, user_id):
        if user_id in departed:
            return None

        async def send(content):
            assert "Den" in content
            calls.append(("dm", user_id))

        return SimpleNamespace(id=user_id, send=AsyncMock(side_effect=send))

    monkeypatch.setattr(purge_module, "guild_member", guild_member)
    gc = SimpleNamespace(log=AsyncMock())
    monkeypatch.setattr(purge_module.GuildContext, "from_interaction", lambda _i: gc)
    return meta, interaction, record, gc, calls


def _boot_purge(n: int = 2, spared: int = 0) -> LurkerPurge:
    purge = _many(n, spared=spared)
    purge.message_id = 555
    purge.boot_posted_at = NOW
    return purge


async def test_boot_denied_for_non_staff(monkeypatch):
    meta, interaction, record, _, calls = _boot_setup(
        monkeypatch, purge=_boot_purge(), roles=()
    )
    await handle_boot(interaction)
    assert _response_type(interaction) == hikari.ResponseType.MESSAGE_CREATE
    assert meta.purge is not None
    assert calls == []
    record.assert_not_called()


async def test_boot_before_final_rejected(monkeypatch):
    purge = _boot_purge()
    purge.boot_posted_at = None
    meta, interaction, _, _, calls = _boot_setup(monkeypatch, purge=purge)
    await handle_boot(interaction)
    assert _response_type(interaction) == hikari.ResponseType.MESSAGE_CREATE
    assert meta.purge is purge
    assert calls == []


async def test_boot_second_press_is_rejected(monkeypatch):
    meta, interaction, _, _, _ = _boot_setup(monkeypatch, purge=_boot_purge())
    await handle_boot(interaction)
    assert meta.purge is None
    assert _response_type(interaction) == hikari.ResponseType.MESSAGE_UPDATE
    assert interaction.app.rest.kick_user.await_count == 2
    await handle_boot(interaction)
    assert _response_type(interaction) == hikari.ResponseType.MESSAGE_CREATE
    assert interaction.app.rest.kick_user.await_count == 2


async def test_boot_update_strips_components(monkeypatch):
    _, interaction, _, _, _ = _boot_setup(monkeypatch, purge=_boot_purge())
    await handle_boot(interaction)
    kwargs = interaction.create_initial_response.await_args_list[0].kwargs
    assert kwargs["components"] == []


async def test_boot_dms_before_kick(monkeypatch):
    _, interaction, _, _, calls = _boot_setup(monkeypatch, purge=_boot_purge())
    await handle_boot(interaction)
    assert calls == [("dm", 1), ("kick", 1), ("dm", 2), ("kick", 2)]


async def test_boot_dm_failure_still_kicks(monkeypatch):
    _, interaction, _, _, calls = _boot_setup(monkeypatch, purge=_boot_purge(1))

    async def guild_member(_bot, _guild_id, user_id):
        return SimpleNamespace(
            id=user_id,
            send=AsyncMock(side_effect=hikari.ForbiddenError("u", {}, b"")),
        )

    monkeypatch.setattr(purge_module, "guild_member", guild_member)
    await handle_boot(interaction)
    assert calls == [("kick", 1)]


async def test_boot_skips_spared(monkeypatch):
    _, interaction, record, _, calls = _boot_setup(
        monkeypatch, purge=_boot_purge(3, spared=1)
    )
    await handle_boot(interaction)
    assert [c for c in calls if c[0] == "kick"] == [("kick", 2), ("kick", 3)]
    assert [c.kwargs["user_id"] for c in record.call_args_list] == [2, 3]


async def test_boot_departed_member_not_dmed_or_kicked(monkeypatch):
    _, interaction, record, gc, calls = _boot_setup(
        monkeypatch, purge=_boot_purge(), departed=(1,)
    )
    await handle_boot(interaction)
    assert calls == [("dm", 2), ("kick", 2)]
    assert [c.kwargs["user_id"] for c in record.call_args_list] == [2]
    summary = gc.log.await_args.args[0]
    assert "c1" in summary.split("left")[1]


async def test_boot_kick_forbidden_continues_and_reports(monkeypatch):
    _, interaction, record, gc, calls = _boot_setup(monkeypatch, purge=_boot_purge())

    async def kick_user(_guild_id, user_id, *, reason):
        if user_id == 1:
            raise hikari.ForbiddenError("u", {}, b"")
        calls.append(("kick", user_id))

    interaction.app.rest.kick_user = AsyncMock(side_effect=kick_user)
    await handle_boot(interaction)
    assert ("kick", 2) in calls
    assert [c.kwargs["user_id"] for c in record.call_args_list] == [2]
    summary = gc.log.await_args.args[0]
    assert summary.startswith("💨")
    kicked_part, failed_part = summary.split("couldn't")
    assert "c2" in kicked_part
    assert "c1" not in kicked_part
    assert "c1" in failed_part
    assert "Kick Members" in summary


async def test_boot_journal_and_audit_reason(monkeypatch):
    _, interaction, record, _, _ = _boot_setup(monkeypatch, purge=_boot_purge(1))
    await handle_boot(interaction)
    reason = interaction.app.rest.kick_user.await_args.kwargs["reason"]
    assert reason == "Lurker purge — booted by Staffer"
    kwargs = record.call_args.kwargs
    assert kwargs["kind"] == "lurker_kicked"
    assert kwargs["user_id"] == 1
    assert kwargs["summary"] == "Booted in the lurker purge by Staffer"


def _lurker_cmd(**options):
    cmd = activity_config.LurkerConfig()
    values = dict.fromkeys(("role", "enabled", "frequency", "grace", "staff_role"))
    values.update(options)
    for name, value in values.items():
        setattr(cmd, name, value)
    return cmd


def _lurker_setup(monkeypatch, tmp_path, *, log_channel=555, **config):
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._config_cache.clear()
    meta = activity_state.load_config(1)
    for key, value in config.items():
        setattr(meta.config, key, value)
    activity_state.save_config(meta)

    gc = MagicMock()
    gc.name = "Guild"
    gc.log = AsyncMock()
    gc.log_channel_id = log_channel
    gc.bot = MagicMock()
    monkeypatch.setattr(
        activity_config.GuildContext, "from_ctx", MagicMock(return_value=gc)
    )
    monkeypatch.setattr(
        activity_config, "check_role_manageable", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        activity_config, "check_guild_perms", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(activity_config, "guild_members", AsyncMock(return_value=[]))
    start = AsyncMock()
    cancel = AsyncMock()
    monkeypatch.setattr(activity_config, "start_purge", start)
    monkeypatch.setattr(activity_config, "cancel_purge", cancel)

    ctx = MagicMock()
    ctx.guild_id = hikari.Snowflake(1)
    ctx.respond = AsyncMock()
    ctx.member.display_name = "Owner"
    return ctx, gc, start, cancel


def _role(role_id: int = ROLE):
    role = MagicMock()
    role.id = hikari.Snowflake(role_id)
    role.name = "Lurker"
    return role


async def test_lurker_config_refuses_enable_without_log_channel(monkeypatch, tmp_path):
    ctx, _, start, _ = _lurker_setup(
        monkeypatch, tmp_path, log_channel=None, lurker_role_id=ROLE
    )
    await _lurker_cmd(enabled=True).invoke(ctx)
    assert not activity_state.load_config(1).config.purge_enabled
    assert "log" in ctx.respond.await_args.args[0]
    start.assert_not_awaited()


async def test_lurker_config_refuses_enable_without_role(monkeypatch, tmp_path):
    ctx, _, start, _ = _lurker_setup(monkeypatch, tmp_path)
    await _lurker_cmd(enabled=True).invoke(ctx)
    assert not activity_state.load_config(1).config.purge_enabled
    assert "lurker role" in ctx.respond.await_args.args[0]
    start.assert_not_awaited()


async def test_lurker_config_enable_starts_purge(monkeypatch, tmp_path):
    ctx, _, start, _ = _lurker_setup(monkeypatch, tmp_path, lurker_role_id=ROLE)
    await _lurker_cmd(enabled=True).invoke(ctx)
    assert activity_state.load_config(1).config.purge_enabled
    start.assert_awaited_once()
    assert start.await_args.args[3].tzinfo is not None


async def test_lurker_config_enable_with_role_in_same_call(monkeypatch, tmp_path):
    ctx, _, start, _ = _lurker_setup(monkeypatch, tmp_path)
    await _lurker_cmd(enabled=True, role=_role()).invoke(ctx)
    config = activity_state.load_config(1).config
    assert config.purge_enabled
    assert config.lurker_role_id == ROLE
    start.assert_awaited_once()


async def test_lurker_config_disable_cancels_running_purge(monkeypatch, tmp_path):
    ctx, _, start, cancel = _lurker_setup(
        monkeypatch, tmp_path, lurker_role_id=ROLE, purge_enabled=True
    )
    meta = activity_state.load_config(1)
    meta.purge = _purge()
    activity_state.save_config(meta)
    await _lurker_cmd(enabled=False).invoke(ctx)
    assert not activity_state.load_config(1).config.purge_enabled
    cancel.assert_awaited_once()
    start.assert_not_awaited()


async def test_lurker_config_partial_update_keeps_other_fields(monkeypatch, tmp_path):
    ctx, _, start, _ = _lurker_setup(
        monkeypatch,
        tmp_path,
        purge_enabled=True,
        purge_interval_months=6,
        purge_staff_role_id=77,
    )
    await _lurker_cmd(grace=7).invoke(ctx)
    config = activity_state.load_config(1).config
    assert config.purge_grace_days == 7
    assert config.purge_interval_months == 6
    assert config.purge_staff_role_id == 77
    start.assert_not_awaited()
