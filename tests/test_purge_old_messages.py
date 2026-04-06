from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import hikari

from dragonpaw_bot.context import ChannelContext

GUILD = "test-guild"
CHANNEL = "test-channel"
CHANNEL_ID = 42


def _msg(age_hours: float, *, pinned: bool = False) -> Mock:
    """Create a mock message with a created_at offset from now."""
    msg = Mock(spec=hikari.Message)
    msg.id = hikari.Snowflake(int(age_hours * 1000))
    msg.created_at = datetime.now(UTC) - timedelta(hours=age_hours)
    msg.is_pinned = pinned
    return msg


def _make_cc(
    *messages: Mock, fetch_side_effect: Exception | None = None
) -> ChannelContext:
    bot = Mock()
    if fetch_side_effect:
        bot.rest.fetch_messages = Mock(side_effect=fetch_side_effect)
    else:

        async def _async_gen(*args, **kwargs):
            before = kwargs.get("before")
            for m in messages:
                if before is None or m.created_at < before:
                    yield m

        bot.rest.fetch_messages = Mock(side_effect=_async_gen)
    bot.rest.delete_messages = AsyncMock()
    bot.rest.delete_message = AsyncMock()

    cc = ChannelContext(
        bot=bot,
        guild_id=hikari.Snowflake(1),
        name=GUILD,
        log_channel_id=None,
        channel_id=hikari.Snowflake(CHANNEL_ID),
        channel_name=CHANNEL,
    )
    return cc


# ---------------------------------------------------------------------------- #
#                              No messages to delete                           #
# ---------------------------------------------------------------------------- #


async def test_no_expired_messages_returns_zero():
    recent = _msg(age_hours=1)  # 1h old, expiry is 2h → keep it
    cc = _make_cc(recent)
    count = await cc.purge_old_messages(expiry_minutes=120)
    assert count == 0
    cc.bot.rest.delete_messages.assert_not_called()
    cc.bot.rest.delete_message.assert_not_called()


# ---------------------------------------------------------------------------- #
#                            Bulk delete (< 14 days)                          #
# ---------------------------------------------------------------------------- #


async def test_bulk_delete_for_messages_within_14_days():
    msg = _msg(age_hours=48)  # 2 days old, expiry 1h → delete via bulk
    cc = _make_cc(msg)
    count = await cc.purge_old_messages(expiry_minutes=60)
    assert count == 1
    cc.bot.rest.delete_messages.assert_called_once()
    cc.bot.rest.delete_message.assert_not_called()


async def test_bulk_delete_batches_at_100():
    msgs = [_msg(age_hours=48) for _ in range(150)]
    # Give each a unique id
    for i, m in enumerate(msgs):
        m.id = hikari.Snowflake(i + 1)
    cc = _make_cc(*msgs)
    count = await cc.purge_old_messages(expiry_minutes=60)
    assert count == 150
    assert cc.bot.rest.delete_messages.call_count == 2  # 100 + 50


# ---------------------------------------------------------------------------- #
#                         Single delete (> 14 days)                           #
# ---------------------------------------------------------------------------- #


async def test_single_delete_for_messages_older_than_14_days():
    msg = _msg(age_hours=24 * 15)  # 15 days old → single delete
    cc = _make_cc(msg)
    count = await cc.purge_old_messages(expiry_minutes=60)
    assert count == 1
    cc.bot.rest.delete_message.assert_called_once()
    cc.bot.rest.delete_messages.assert_not_called()


async def test_not_found_in_single_delete_is_swallowed():
    msg = _msg(age_hours=24 * 15)
    cc = _make_cc(msg)
    cc.bot.rest.delete_message = AsyncMock(
        side_effect=hikari.NotFoundError(url="x", headers={}, raw_body=b"")
    )
    count = await cc.purge_old_messages(expiry_minutes=60)
    assert count == 1  # Still counted even if already gone


# ---------------------------------------------------------------------------- #
#                         Mixed bulk + single                                  #
# ---------------------------------------------------------------------------- #


async def test_mixed_bulk_and_single():
    bulk_msg = _msg(age_hours=48)  # < 14 days → bulk
    single_msg = _msg(age_hours=24 * 20)  # > 14 days → single
    cc = _make_cc(bulk_msg, single_msg)
    count = await cc.purge_old_messages(expiry_minutes=60)
    assert count == 2
    cc.bot.rest.delete_messages.assert_called_once()
    cc.bot.rest.delete_message.assert_called_once()


# ---------------------------------------------------------------------------- #
#                           Error handling                                     #
# ---------------------------------------------------------------------------- #


async def test_forbidden_on_fetch_returns_zero():
    cc = _make_cc(
        fetch_side_effect=hikari.ForbiddenError(url="x", headers={}, raw_body=b"")
    )
    count = await cc.purge_old_messages(expiry_minutes=60)
    assert count == 0
    cc.bot.rest.delete_messages.assert_not_called()
    cc.bot.rest.delete_message.assert_not_called()


async def test_not_found_on_fetch_returns_zero():
    cc = _make_cc(
        fetch_side_effect=hikari.NotFoundError(url="x", headers={}, raw_body=b"")
    )
    count = await cc.purge_old_messages(expiry_minutes=60)
    assert count == 0


async def test_forbidden_on_bulk_delete_breaks_loop():
    msgs = [_msg(age_hours=48) for _ in range(3)]
    for i, m in enumerate(msgs):
        m.id = hikari.Snowflake(i + 1)
    cc = _make_cc(*msgs)
    cc.bot.rest.delete_messages = AsyncMock(
        side_effect=hikari.ForbiddenError(url="x", headers={}, raw_body=b"")
    )
    # Should not raise; error is logged and loop breaks
    count = await cc.purge_old_messages(expiry_minutes=60)
    assert count == 3  # Still counted in return value
    assert cc.bot.rest.delete_messages.call_count == 1  # Stopped after first failure


async def test_forbidden_on_single_delete_breaks_loop():
    msgs = [_msg(age_hours=24 * 20) for _ in range(3)]
    for i, m in enumerate(msgs):
        m.id = hikari.Snowflake(i + 1)
    cc = _make_cc(*msgs)
    cc.bot.rest.delete_message = AsyncMock(
        side_effect=hikari.ForbiddenError(url="x", headers={}, raw_body=b"")
    )
    count = await cc.purge_old_messages(expiry_minutes=60)
    assert count == 3
    assert cc.bot.rest.delete_message.call_count == 1  # Stopped after first failure


async def test_single_delete_limit_caps_deletes():
    # 1500 old messages, default limit of 1000 — only 1000 should be attempted
    msgs = [_msg(age_hours=24 * 20) for _ in range(1500)]
    for i, m in enumerate(msgs):
        m.id = hikari.Snowflake(i + 1)
    cc = _make_cc(*msgs)
    count = await cc.purge_old_messages(expiry_minutes=60)
    assert count == 1000  # Capped; remainder picked up next run
    assert cc.bot.rest.delete_message.call_count == 1000


async def test_pinned_messages_are_skipped():
    pinned = _msg(age_hours=48, pinned=True)
    normal = _msg(age_hours=49)
    cc = _make_cc(pinned, normal)
    count = await cc.purge_old_messages(expiry_minutes=60)
    assert count == 1
    # Only the non-pinned message should be in the bulk delete
    cc.bot.rest.delete_messages.assert_called_once()
    (call_args,) = cc.bot.rest.delete_messages.call_args_list
    assert normal.id in call_args[0][1]
    assert pinned.id not in call_args[0][1]


async def test_pinned_old_messages_are_skipped():
    """Pinned messages older than 14 days should also be skipped."""
    pinned = _msg(age_hours=24 * 20, pinned=True)
    normal = _msg(age_hours=24 * 20)
    cc = _make_cc(pinned, normal)
    count = await cc.purge_old_messages(expiry_minutes=60)
    assert count == 1
    cc.bot.rest.delete_message.assert_called_once()


async def test_single_delete_limit_custom():
    msgs = [_msg(age_hours=24 * 20) for _ in range(50)]
    for i, m in enumerate(msgs):
        m.id = hikari.Snowflake(i + 1)
    cc = _make_cc(*msgs)
    count = await cc.purge_old_messages(expiry_minutes=60, single_delete_limit=10)
    assert count == 10
    assert cc.bot.rest.delete_message.call_count == 10


# ---------------------------------------------------------------------------- #
#                            purge_old_threads tests                           #
# ---------------------------------------------------------------------------- #


def _thread(
    age_hours: float,
    *,
    has_messages: bool = True,
    channel_id: int = CHANNEL_ID,
) -> Mock:
    """Create a mock GuildPublicThread."""
    thread = Mock(spec=hikari.GuildPublicThread)
    thread.id = hikari.Snowflake(int(age_hours * 10000 + 100_000))
    thread.name = f"thread-{age_hours}h"
    thread.parent_id = hikari.Snowflake(channel_id)
    thread.created_at = datetime.now(UTC) - timedelta(hours=age_hours)
    if has_messages:
        last_msg = Mock()
        last_msg.created_at = datetime.now(UTC) - timedelta(hours=age_hours)
        thread.last_message_id = last_msg
    else:
        thread.last_message_id = None
    return thread


def _make_cc_threads(
    active: list[Mock] | None = None,
    archived: list[Mock] | None = None,
    *,
    fetch_active_side_effect: Exception | None = None,
    fetch_archived_side_effect: Exception | None = None,
) -> ChannelContext:
    bot = Mock()

    if fetch_active_side_effect:
        bot.rest.fetch_active_threads = AsyncMock(side_effect=fetch_active_side_effect)
    else:
        bot.rest.fetch_active_threads = AsyncMock(return_value=active or [])

    if fetch_archived_side_effect:
        bot.rest.fetch_public_archived_threads = Mock(
            side_effect=fetch_archived_side_effect
        )
    else:
        _archived = archived or []

        async def _archived_gen(*args, **kwargs):
            for t in _archived:
                yield t

        bot.rest.fetch_public_archived_threads = Mock(side_effect=_archived_gen)

    bot.rest.delete_channel = AsyncMock()
    bot.rest.create_message = AsyncMock()

    return ChannelContext(
        bot=bot,
        guild_id=hikari.Snowflake(1),
        name=GUILD,
        log_channel_id=None,
        channel_id=hikari.Snowflake(CHANNEL_ID),
        channel_name=CHANNEL,
    )


async def test_purge_old_threads_stale_active_thread_deleted():
    t = _thread(age_hours=48)  # 48h old, expiry 1h → delete
    cc = _make_cc_threads(active=[t])
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 1
    cc.bot.rest.delete_channel.assert_called_once_with(t.id)


async def test_purge_old_threads_fresh_active_thread_kept():
    t = _thread(age_hours=0.5)  # 30m old, expiry 2h → keep
    cc = _make_cc_threads(active=[t])
    count = await cc.purge_old_threads(expiry_minutes=120)
    assert count == 0
    cc.bot.rest.delete_channel.assert_not_called()


async def test_purge_old_threads_stale_archived_thread_deleted():
    t = _thread(age_hours=72)  # archived thread, 72h old, expiry 1h
    cc = _make_cc_threads(archived=[t])
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 1
    cc.bot.rest.delete_channel.assert_called_once_with(t.id)


async def test_purge_old_threads_skips_threads_from_other_channels():
    t = _thread(age_hours=48, channel_id=999)  # different parent
    cc = _make_cc_threads(active=[t])
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 0
    cc.bot.rest.delete_channel.assert_not_called()


async def test_purge_old_threads_no_messages_uses_created_at():
    # Thread has no messages; created 48h ago; expiry 1h → stale
    t = _thread(age_hours=48, has_messages=False)
    cc = _make_cc_threads(active=[t])
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 1
    cc.bot.rest.delete_channel.assert_called_once_with(t.id)


async def test_purge_old_threads_not_found_on_delete_swallowed():
    t = _thread(age_hours=48)
    cc = _make_cc_threads(active=[t])
    cc.bot.rest.delete_channel = AsyncMock(
        side_effect=hikari.NotFoundError(url="x", headers={}, raw_body=b"")
    )
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 1  # Still counted as deleted (was already gone)


async def test_purge_old_threads_forbidden_on_delete_breaks_loop_and_logs():
    threads = [_thread(age_hours=48 + i) for i in range(3)]
    cc = _make_cc_threads(active=threads, archived=[])
    cc.bot.rest.delete_channel = AsyncMock(
        side_effect=hikari.ForbiddenError(url="x", headers={}, raw_body=b"")
    )
    cc.log_channel_id = hikari.Snowflake(99)
    count = await cc.purge_old_threads(expiry_minutes=60)
    # Stops after first failure — ForbiddenError fires before deleted += 1
    assert count == 0
    assert cc.bot.rest.delete_channel.call_count == 1
    # Posts to log channel
    cc.bot.rest.create_message.assert_called_once()
    content = cc.bot.rest.create_message.call_args.kwargs["content"]
    assert "Manage Threads" in content


async def test_purge_old_threads_forbidden_on_fetch_active_returns_zero():
    cc = _make_cc_threads(
        fetch_active_side_effect=hikari.ForbiddenError(
            url="x", headers={}, raw_body=b""
        )
    )
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 0
    cc.bot.rest.delete_channel.assert_not_called()


async def test_purge_old_threads_mixed_active_and_archived():
    active_stale = _thread(age_hours=48)
    archived_stale = _thread(age_hours=96)
    fresh = _thread(age_hours=0.5)
    cc = _make_cc_threads(active=[active_stale, fresh], archived=[archived_stale])
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 2
    assert cc.bot.rest.delete_channel.call_count == 2


# ---------------------------------------------------------------------------- #
#                    run_cleanup calls purge_old_threads                       #
# ---------------------------------------------------------------------------- #


async def test_run_cleanup_calls_purge_old_threads(monkeypatch):
    """run_cleanup should purge threads as well as messages."""
    cc = _make_cc_threads()
    monkeypatch.setattr(cc, "check_perms", AsyncMock(return_value=[]))
    mock_msgs = AsyncMock(return_value=0)
    mock_threads = AsyncMock(return_value=0)
    monkeypatch.setattr(cc, "purge_old_messages", mock_msgs)
    monkeypatch.setattr(cc, "purge_old_threads", mock_threads)

    await cc.run_cleanup(expiry_minutes=60)

    mock_msgs.assert_awaited_once_with(60)
    mock_threads.assert_awaited_once_with(60)
