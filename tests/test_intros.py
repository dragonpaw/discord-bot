from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import hikari

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.intros import cron as intros_cron
from dragonpaw_bot.plugins.intros import state as intros_state
from dragonpaw_bot.plugins.intros.cron import (
    IntrosScanResult,
    _classify_members,
    _cleanup_messages,
    _daily_guild,
    _reconcile_missing,
    _set_role,
    _sync_missing_role,
    scan_intros,
)
from dragonpaw_bot.plugins.intros.models import IntrosGuildState

GUILD_ID = 1
LOG_CHANNEL_ID = 555
INTROS_CHANNEL_ID = 77
REQUIRED_ROLE_ID = 10
MISSING_ROLE_ID = 20


def _forbidden() -> hikari.ForbiddenError:
    return hikari.ForbiddenError(url="", headers={}, raw_body=b"")


def _not_found() -> hikari.NotFoundError:
    return hikari.NotFoundError(url="", headers={}, raw_body=b"")


def _member(
    member_id: int,
    *,
    role_ids: tuple[int, ...] = (),
    is_bot: bool = False,
    name: str | None = None,
) -> MagicMock:
    m = MagicMock(spec=hikari.Member)
    m.id = hikari.Snowflake(member_id)
    m.role_ids = [hikari.Snowflake(r) for r in role_ids]
    m.is_bot = is_bot
    m.display_name = name or f"user{member_id}"
    m.mention = f"<@{member_id}>"
    return m


def _message(
    author_id: int,
    *,
    pinned: bool = False,
    is_bot: bool = False,
    display_name: str | None = None,
) -> MagicMock:
    msg = MagicMock(spec=hikari.Message)
    msg.author = MagicMock(spec=hikari.User)
    msg.author.id = hikari.Snowflake(author_id)
    msg.author.is_bot = is_bot
    msg.author.display_name = display_name or f"user{author_id}"
    msg.is_pinned = pinned
    msg.delete = AsyncMock()
    return msg


def _aiter(items):
    async def gen():
        for item in items:
            yield item

    return gen()


def _bot(
    *,
    members: tuple[MagicMock, ...] = (),
    messages: tuple[MagicMock, ...] = (),
    general_channel_id: int | None = None,
) -> MagicMock:
    """A bot whose only real behaviour is the Discord cache/REST boundary."""
    bot = MagicMock()
    bot.state.return_value = SimpleNamespace(
        log_channel_id=hikari.Snowflake(LOG_CHANNEL_ID),
        general_channel_id=general_channel_id,
    )
    bot.cache.get_members_view_for_guild.return_value = {m.id: m for m in members}
    bot.cache.get_member.return_value = None
    bot.rest.create_message = AsyncMock()
    bot.rest.add_role_to_member = AsyncMock()
    bot.rest.remove_role_from_member = AsyncMock()
    bot.rest.fetch_member = AsyncMock(side_effect=_not_found())
    bot.rest.fetch_messages = MagicMock(return_value=_aiter(messages))
    return bot


def _gc(bot: MagicMock) -> GuildContext:
    return GuildContext(
        bot=bot,
        guild_id=hikari.Snowflake(GUILD_ID),
        name="TestGuild",
        log_channel_id=hikari.Snowflake(LOG_CHANNEL_ID),
    )


def _state(**kwargs) -> IntrosGuildState:
    return IntrosGuildState(
        guild_id=GUILD_ID,
        guild_name="TestGuild",
        channel_id=INTROS_CHANNEL_ID,
        channel_name="introductions",
        **kwargs,
    )


def _logged(bot: MagicMock) -> list[str]:
    return [c.kwargs["content"] for c in bot.rest.create_message.await_args_list]


# ---------------------------------------------------------------------------- #
#                            _classify_members                                 #
# ---------------------------------------------------------------------------- #


def test_classify_skips_bots():
    """A bot with no intro must never be flagged missing."""
    st = _state(missing_role_id=MISSING_ROLE_ID)
    robot = _member(2, role_ids=(MISSING_ROLE_ID,), is_bot=True)

    result, holders = _classify_members(st, [robot], set())

    assert result.missing == []
    assert holders == []


def test_classify_excludes_members_without_required_role():
    """The required-role filter decides eligibility on its own."""
    st = _state(required_role_id=REQUIRED_ROLE_ID)
    eligible = _member(2, role_ids=(REQUIRED_ROLE_ID,))
    outsider = _member(3)

    result, _ = _classify_members(st, [eligible, outsider], set())

    assert result.missing == [eligible]


def test_classify_ignores_ineligible_role_holder():
    """A validation-seeded holder without the required role is not a reconcile target."""
    st = _state(required_role_id=REQUIRED_ROLE_ID, missing_role_id=MISSING_ROLE_ID)
    seeded = _member(2, role_ids=(MISSING_ROLE_ID,))

    result, holders = _classify_members(st, [seeded], set())

    assert result.missing == []
    assert holders == []


def test_classify_without_required_role_includes_everyone():
    st = _state()
    a = _member(2)
    b = _member(3, role_ids=(REQUIRED_ROLE_ID,))

    result, _ = _classify_members(st, [a, b], set())

    assert result.missing == [a, b]


def test_classify_poster_is_holder_but_not_missing():
    """The member who drives a role removal: has the role, already posted."""
    st = _state(missing_role_id=MISSING_ROLE_ID)
    poster = _member(2, role_ids=(MISSING_ROLE_ID,))

    result, holders = _classify_members(st, [poster], {2})

    assert result.missing == []
    assert holders == [poster]


# ---------------------------------------------------------------------------- #
#                       _sync_missing_role / _set_role                         #
# ---------------------------------------------------------------------------- #


async def test_sync_adds_role_to_member_missing_intro():
    bot = _bot()
    st = _state(missing_role_id=MISSING_ROLE_ID)
    straggler = _member(2)
    result = IntrosScanResult(missing=[straggler])

    await _sync_missing_role(_gc(bot), st, result, [])

    bot.rest.add_role_to_member.assert_awaited_once_with(
        hikari.Snowflake(GUILD_ID), straggler.id, MISSING_ROLE_ID
    )
    assert result.role_added == [straggler]


async def test_sync_does_not_re_add_role_to_existing_holder():
    bot = _bot()
    st = _state(missing_role_id=MISSING_ROLE_ID)
    straggler = _member(2, role_ids=(MISSING_ROLE_ID,))
    result = IntrosScanResult(missing=[straggler])

    await _sync_missing_role(_gc(bot), st, result, [straggler])

    bot.rest.add_role_to_member.assert_not_awaited()
    assert result.role_added == []


async def test_sync_removes_role_from_holder_who_posted():
    bot = _bot()
    st = _state(missing_role_id=MISSING_ROLE_ID)
    poster = _member(2, role_ids=(MISSING_ROLE_ID,))
    result = IntrosScanResult()

    await _sync_missing_role(_gc(bot), st, result, [poster])

    bot.rest.remove_role_from_member.assert_awaited_once_with(
        hikari.Snowflake(GUILD_ID), poster.id, MISSING_ROLE_ID
    )
    assert result.role_removed == [poster]


async def test_sync_keeps_role_on_holder_still_missing():
    bot = _bot()
    st = _state(missing_role_id=MISSING_ROLE_ID)
    holder = _member(2, role_ids=(MISSING_ROLE_ID,))
    result = IntrosScanResult(missing=[holder])

    await _sync_missing_role(_gc(bot), st, result, [holder])

    bot.rest.remove_role_from_member.assert_not_awaited()
    assert result.role_removed == []


async def test_sync_bails_out_after_permission_failure():
    """A hierarchy problem blocks every later change, so stop after the first."""
    bot = _bot()
    bot.rest.add_role_to_member.side_effect = _forbidden()
    st = _state(missing_role_id=MISSING_ROLE_ID)
    first, second = _member(2), _member(3)
    result = IntrosScanResult(missing=[first, second])

    await _sync_missing_role(_gc(bot), st, result, [])

    assert bot.rest.add_role_to_member.await_count == 1
    assert result.role_failed is True
    assert result.role_added == []


async def test_sync_continues_past_http_error():
    """A transient HTTP error affects one member only — keep syncing the rest."""
    bot = _bot()
    bot.rest.add_role_to_member.side_effect = [hikari.HTTPError("boom"), None]
    st = _state(missing_role_id=MISSING_ROLE_ID)
    first, second = _member(2), _member(3)
    result = IntrosScanResult(missing=[first, second])

    await _sync_missing_role(_gc(bot), st, result, [])

    assert bot.rest.add_role_to_member.await_count == 2
    assert result.role_failed is False
    assert result.role_added == [second]


async def test_set_role_treats_departed_member_as_no_change():
    bot = _bot()
    bot.rest.add_role_to_member.side_effect = _not_found()
    st = _state(missing_role_id=MISSING_ROLE_ID)
    result = IntrosScanResult()

    changed = await _set_role(_gc(bot), st, result, _member(2), add=True)

    assert changed is False
    assert result.role_failed is False


async def test_set_role_removes_via_remove_endpoint():
    bot = _bot()
    st = _state(missing_role_id=MISSING_ROLE_ID)
    member = _member(2)

    changed = await _set_role(_gc(bot), st, IntrosScanResult(), member, add=False)

    assert changed is True
    bot.rest.remove_role_from_member.assert_awaited_once_with(
        hikari.Snowflake(GUILD_ID), member.id, MISSING_ROLE_ID
    )
    bot.rest.add_role_to_member.assert_not_awaited()


# ---------------------------------------------------------------------------- #
#                                scan_intros                                   #
# ---------------------------------------------------------------------------- #


async def test_scan_ignores_pinned_and_bot_messages_when_fetching():
    """Pinned prompts and bot posts don't count as anyone's introduction."""
    pinned = _message(2, pinned=True)
    from_bot = _message(3, is_bot=True)
    bot = _bot(messages=(pinned, from_bot))
    st = _state()
    human, robot_owner = _member(2), _member(3)

    result = await scan_intros(_gc(bot), st, members=[human, robot_owner])

    assert result.missing == [human, robot_owner]


async def test_scan_leaves_ineligible_role_holder_alone():
    """The validation-seeded gate survives reconciliation: no role change either way."""
    bot = _bot()
    st = _state(required_role_id=REQUIRED_ROLE_ID, missing_role_id=MISSING_ROLE_ID)
    seeded = _member(2, role_ids=(MISSING_ROLE_ID,))

    result = await scan_intros(_gc(bot), st, members=[seeded], posted_ids={2})

    assert result.role_removed == []
    bot.rest.remove_role_from_member.assert_not_awaited()
    bot.rest.add_role_to_member.assert_not_awaited()


async def test_scan_skips_role_sync_when_no_missing_role_configured():
    bot = _bot()
    st = _state()

    result = await scan_intros(_gc(bot), st, members=[_member(2)], posted_ids=set())

    assert len(result.missing) == 1
    bot.rest.add_role_to_member.assert_not_awaited()


# ---------------------------------------------------------------------------- #
#                             _cleanup_messages                                #
# ---------------------------------------------------------------------------- #


async def test_cleanup_deletes_departed_members_post():
    departed = _message(2, display_name="Gone")
    bot = _bot()

    await _cleanup_messages(_gc(bot), [], [departed])

    departed.delete.assert_awaited_once()
    assert "Gone" in _logged(bot)[0]
    assert _logged(bot)[0].startswith("🧹")


async def test_cleanup_keeps_post_when_rest_confirms_member_present():
    """A cache miss is not a departure — confirm over REST before deleting."""
    msg = _message(2)
    bot = _bot()
    bot.rest.fetch_member = AsyncMock(return_value=_member(2))

    await _cleanup_messages(_gc(bot), [], [msg])

    msg.delete.assert_not_awaited()
    bot.rest.create_message.assert_not_awaited()


async def test_cleanup_confirms_each_departed_author_only_once():
    first, second = _message(2), _message(2)
    bot = _bot()

    await _cleanup_messages(_gc(bot), [], [first, second])

    assert bot.rest.fetch_member.await_count == 1
    first.delete.assert_awaited_once()
    second.delete.assert_awaited_once()


async def test_cleanup_deletes_older_duplicate_keeps_newest():
    """Messages arrive newest-first, so the first one seen per author is the keeper."""
    newest, oldest = _message(2), _message(2)
    member = _member(2)
    bot = _bot(members=(member,))

    await _cleanup_messages(_gc(bot), [member], [newest, oldest])

    newest.delete.assert_not_awaited()
    oldest.delete.assert_awaited_once()
    assert _logged(bot)[0].startswith("✂️")


async def test_cleanup_skips_pinned_messages():
    pinned = _message(2, pinned=True)
    bot = _bot()

    await _cleanup_messages(_gc(bot), [], [pinned])

    pinned.delete.assert_not_awaited()
    bot.rest.create_message.assert_not_awaited()


async def test_cleanup_reports_nothing_when_delete_is_forbidden():
    departed = _message(2)
    departed.delete.side_effect = _forbidden()
    bot = _bot()

    await _cleanup_messages(_gc(bot), [], [departed])

    bot.rest.create_message.assert_not_awaited()


# ---------------------------------------------------------------------------- #
#                            _reconcile_missing                                #
# ---------------------------------------------------------------------------- #


async def test_reconcile_stays_quiet_when_no_roles_changed():
    bot = _bot()
    st = _state(missing_role_id=MISSING_ROLE_ID)
    poster = _member(2)

    await _reconcile_missing(_gc(bot), st, [poster], {2})

    bot.rest.create_message.assert_not_awaited()


async def test_reconcile_reports_role_hierarchy_failure():
    bot = _bot()
    bot.rest.add_role_to_member.side_effect = _forbidden()
    st = _state(missing_role_id=MISSING_ROLE_ID, missing_role_name="Shy")

    await _reconcile_missing(_gc(bot), st, [_member(2)], set())

    assert "role hierarchy" in _logged(bot)[0]


# ---------------------------------------------------------------------------- #
#                               _daily_guild                                   #
# ---------------------------------------------------------------------------- #


def _guild() -> SimpleNamespace:
    return SimpleNamespace(id=hikari.Snowflake(GUILD_ID), name="TestGuild")


async def test_daily_guild_skips_everything_without_channel_permissions(
    tmp_path, monkeypatch
):
    """Without READ_MESSAGE_HISTORY the message list would be empty — never act on it."""
    monkeypatch.setattr(intros_state.store, "state_dir", tmp_path)
    intros_state.store.cache.clear()
    intros_state.save(_state(missing_role_id=MISSING_ROLE_ID))
    monkeypatch.setattr(
        intros_cron, "check_channel_perms", AsyncMock(return_value=["Manage Messages"])
    )

    bot = _bot(members=(_member(2),))
    await _daily_guild(bot, _guild())

    bot.rest.fetch_messages.assert_not_called()
    bot.rest.add_role_to_member.assert_not_awaited()
    assert "Manage Messages" in _logged(bot)[0]


async def test_daily_guild_cleans_and_reconciles_from_one_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(intros_state.store, "state_dir", tmp_path)
    intros_state.store.cache.clear()
    intros_state.save(_state(missing_role_id=MISSING_ROLE_ID))
    monkeypatch.setattr(intros_cron, "check_channel_perms", AsyncMock(return_value=[]))

    poster = _member(2, role_ids=(MISSING_ROLE_ID,))
    straggler = _member(3)
    departed_post = _message(4, display_name="Gone")
    bot = _bot(members=(poster, straggler), messages=(departed_post, _message(2)))

    await _daily_guild(bot, _guild())

    assert bot.rest.fetch_messages.call_count == 1
    departed_post.delete.assert_awaited_once()
    bot.rest.add_role_to_member.assert_awaited_once_with(
        hikari.Snowflake(GUILD_ID), straggler.id, MISSING_ROLE_ID
    )
    bot.rest.remove_role_from_member.assert_awaited_once_with(
        hikari.Snowflake(GUILD_ID), poster.id, MISSING_ROLE_ID
    )


async def test_daily_guild_ignores_pinned_and_bot_posts_when_reconciling(
    tmp_path, monkeypatch
):
    """A pinned prompt or the bot's own chatter must not count as someone's intro."""
    monkeypatch.setattr(intros_state.store, "state_dir", tmp_path)
    intros_state.store.cache.clear()
    intros_state.save(_state(missing_role_id=MISSING_ROLE_ID))
    monkeypatch.setattr(intros_cron, "check_channel_perms", AsyncMock(return_value=[]))

    pinner, bot_author = _member(5), _member(6)
    bot = _bot(
        members=(pinner, bot_author),
        messages=(_message(5, pinned=True), _message(6, is_bot=True)),
    )

    await _daily_guild(bot, _guild())

    assert {c.args[1] for c in bot.rest.add_role_to_member.await_args_list} == {
        pinner.id,
        bot_author.id,
    }


async def test_daily_guild_returns_when_channel_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(intros_state.store, "state_dir", tmp_path)
    intros_state.store.cache.clear()
    perms = AsyncMock(return_value=[])
    monkeypatch.setattr(intros_cron, "check_channel_perms", perms)

    bot = _bot()
    await _daily_guild(bot, _guild())

    perms.assert_not_awaited()
    bot.rest.fetch_messages.assert_not_called()


# ---------------------------------------------------------------------------- #
#                            State persistence                                 #
# ---------------------------------------------------------------------------- #


def test_state_yaml_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(intros_state.store, "state_dir", tmp_path)
    intros_state.store.cache.clear()

    intros_state.save(
        _state(
            required_role_id=REQUIRED_ROLE_ID,
            required_role_name="Verified",
            missing_role_id=MISSING_ROLE_ID,
            missing_role_name="Shy",
        )
    )

    intros_state.store.cache.clear()
    loaded = intros_state.load(GUILD_ID)

    assert loaded.channel_id == INTROS_CHANNEL_ID
    assert loaded.channel_name == "introductions"
    assert loaded.required_role_id == REQUIRED_ROLE_ID
    assert loaded.required_role_name == "Verified"
    assert loaded.missing_role_id == MISSING_ROLE_ID
    assert loaded.missing_role_name == "Shy"


def test_load_returns_unconfigured_state_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(intros_state.store, "state_dir", tmp_path)
    intros_state.store.cache.clear()

    loaded = intros_state.load(999)

    assert loaded.guild_id == 999
    assert loaded.channel_id is None
    assert loaded.missing_role_id is None
