from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import hikari

from dragonpaw_bot.utils import purge_old_messages

GUILD = "test-guild"
CHANNEL = "test-channel"
CHANNEL_ID = 42


def _msg(age_hours: float) -> Mock:
    """Create a mock message with a created_at offset from now."""
    msg = Mock(spec=hikari.Message)
    msg.id = hikari.Snowflake(int(age_hours * 1000))
    msg.created_at = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return msg


def _make_bot(*messages: Mock, fetch_side_effect: Exception | None = None) -> Mock:
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
    return bot


# ---------------------------------------------------------------------------- #
#                              No messages to delete                           #
# ---------------------------------------------------------------------------- #


async def test_no_expired_messages_returns_zero():
    recent = _msg(age_hours=1)  # 1h old, expiry is 2h → keep it
    bot = _make_bot(recent)
    count = await purge_old_messages(bot, GUILD, CHANNEL, CHANNEL_ID, expiry_minutes=120)
    assert count == 0
    bot.rest.delete_messages.assert_not_called()
    bot.rest.delete_message.assert_not_called()


# ---------------------------------------------------------------------------- #
#                            Bulk delete (< 14 days)                          #
# ---------------------------------------------------------------------------- #


async def test_bulk_delete_for_messages_within_14_days():
    msg = _msg(age_hours=48)  # 2 days old, expiry 1h → delete via bulk
    bot = _make_bot(msg)
    count = await purge_old_messages(bot, GUILD, CHANNEL, CHANNEL_ID, expiry_minutes=60)
    assert count == 1
    bot.rest.delete_messages.assert_called_once()
    bot.rest.delete_message.assert_not_called()


async def test_bulk_delete_batches_at_100():
    msgs = [_msg(age_hours=48) for _ in range(150)]
    # Give each a unique id
    for i, m in enumerate(msgs):
        m.id = hikari.Snowflake(i + 1)
    bot = _make_bot(*msgs)
    count = await purge_old_messages(bot, GUILD, CHANNEL, CHANNEL_ID, expiry_minutes=60)
    assert count == 150
    assert bot.rest.delete_messages.call_count == 2  # 100 + 50


# ---------------------------------------------------------------------------- #
#                         Single delete (> 14 days)                           #
# ---------------------------------------------------------------------------- #


async def test_single_delete_for_messages_older_than_14_days():
    msg = _msg(age_hours=24 * 15)  # 15 days old → single delete
    bot = _make_bot(msg)
    count = await purge_old_messages(bot, GUILD, CHANNEL, CHANNEL_ID, expiry_minutes=60)
    assert count == 1
    bot.rest.delete_message.assert_called_once()
    bot.rest.delete_messages.assert_not_called()


async def test_not_found_in_single_delete_is_swallowed():
    msg = _msg(age_hours=24 * 15)
    bot = _make_bot(msg)
    bot.rest.delete_message = AsyncMock(
        side_effect=hikari.NotFoundError(url="x", headers={}, raw_body=b"")
    )
    count = await purge_old_messages(bot, GUILD, CHANNEL, CHANNEL_ID, expiry_minutes=60)
    assert count == 1  # Still counted even if already gone


# ---------------------------------------------------------------------------- #
#                         Mixed bulk + single                                  #
# ---------------------------------------------------------------------------- #


async def test_mixed_bulk_and_single():
    bulk_msg = _msg(age_hours=48)     # < 14 days → bulk
    single_msg = _msg(age_hours=24 * 20)  # > 14 days → single
    bot = _make_bot(bulk_msg, single_msg)
    count = await purge_old_messages(bot, GUILD, CHANNEL, CHANNEL_ID, expiry_minutes=60)
    assert count == 2
    bot.rest.delete_messages.assert_called_once()
    bot.rest.delete_message.assert_called_once()


# ---------------------------------------------------------------------------- #
#                           Error handling                                     #
# ---------------------------------------------------------------------------- #


async def test_forbidden_on_fetch_returns_zero():
    bot = _make_bot(
        fetch_side_effect=hikari.ForbiddenError(url="x", headers={}, raw_body=b"")
    )
    count = await purge_old_messages(bot, GUILD, CHANNEL, CHANNEL_ID, expiry_minutes=60)
    assert count == 0
    bot.rest.delete_messages.assert_not_called()
    bot.rest.delete_message.assert_not_called()


async def test_not_found_on_fetch_returns_zero():
    bot = _make_bot(
        fetch_side_effect=hikari.NotFoundError(url="x", headers={}, raw_body=b"")
    )
    count = await purge_old_messages(bot, GUILD, CHANNEL, CHANNEL_ID, expiry_minutes=60)
    assert count == 0


async def test_forbidden_on_bulk_delete_breaks_loop():
    msgs = [_msg(age_hours=48) for _ in range(3)]
    for i, m in enumerate(msgs):
        m.id = hikari.Snowflake(i + 1)
    bot = _make_bot(*msgs)
    bot.rest.delete_messages = AsyncMock(
        side_effect=hikari.ForbiddenError(url="x", headers={}, raw_body=b"")
    )
    # Should not raise; error is logged and loop breaks
    count = await purge_old_messages(bot, GUILD, CHANNEL, CHANNEL_ID, expiry_minutes=60)
    assert count == 3  # Still counted in return value
    assert bot.rest.delete_messages.call_count == 1  # Stopped after first failure


async def test_forbidden_on_single_delete_breaks_loop():
    msgs = [_msg(age_hours=24 * 20) for _ in range(3)]
    for i, m in enumerate(msgs):
        m.id = hikari.Snowflake(i + 1)
    bot = _make_bot(*msgs)
    bot.rest.delete_message = AsyncMock(
        side_effect=hikari.ForbiddenError(url="x", headers={}, raw_body=b"")
    )
    count = await purge_old_messages(bot, GUILD, CHANNEL, CHANNEL_ID, expiry_minutes=60)
    assert count == 3
    assert bot.rest.delete_message.call_count == 1  # Stopped after first failure


async def test_single_delete_limit_caps_deletes():
    # 1500 old messages, default limit of 1000 — only 1000 should be attempted
    msgs = [_msg(age_hours=24 * 20) for _ in range(1500)]
    for i, m in enumerate(msgs):
        m.id = hikari.Snowflake(i + 1)
    bot = _make_bot(*msgs)
    count = await purge_old_messages(bot, GUILD, CHANNEL, CHANNEL_ID, expiry_minutes=60)
    assert count == 1000  # Capped; remainder picked up next run
    assert bot.rest.delete_message.call_count == 1000


async def test_single_delete_limit_custom():
    msgs = [_msg(age_hours=24 * 20) for _ in range(50)]
    for i, m in enumerate(msgs):
        m.id = hikari.Snowflake(i + 1)
    bot = _make_bot(*msgs)
    count = await purge_old_messages(
        bot, GUILD, CHANNEL, CHANNEL_ID, expiry_minutes=60, single_delete_limit=10
    )
    assert count == 10
    assert bot.rest.delete_message.call_count == 10
