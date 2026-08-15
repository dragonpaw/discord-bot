import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import hikari
import pytest

from dragonpaw_bot.plugins.media_channels import cron as media_cron
from dragonpaw_bot.plugins.media_channels import state as media_state
from dragonpaw_bot.plugins.media_channels.listeners import on_message
from dragonpaw_bot.plugins.media_channels.models import (
    MediaChannelEntry,
    MediaGuildState,
)
from dragonpaw_bot.utils import message_has_media

# ---------------------------------------------------------------------------- #
#                               _has_media                                     #
# ---------------------------------------------------------------------------- #


def _mock_snapshot(
    content: str | None = None,
    attachments: list | None = None,
    stickers: list | None = None,
) -> Mock:
    snap = Mock(spec=hikari.messages.MessageSnapshot)
    snap.content = content
    snap.attachments = attachments or []
    snap.stickers = stickers or []
    return snap


def _mock_message(
    content: str | None = None,
    attachments: list | None = None,
    stickers: list | None = None,
    snapshots: list | None = None,
) -> Mock:
    msg = Mock(spec=hikari.Message)
    msg.content = content
    msg.attachments = attachments or []
    msg.stickers = stickers or []
    msg.message_snapshots = snapshots or []
    return msg


def test_has_media_with_attachment():
    msg = _mock_message(content=None, attachments=[Mock()])
    assert message_has_media(msg) is True


def test_has_media_with_https_url():
    msg = _mock_message(content="check this out https://example.com")
    assert message_has_media(msg) is True


def test_has_media_with_http_url():
    msg = _mock_message(content="http://example.com/image.png")
    assert message_has_media(msg) is True


def test_has_media_with_sticker():
    msg = _mock_message(stickers=[Mock()])
    assert message_has_media(msg) is True


def test_has_media_plain_text():
    msg = _mock_message(content="just some text")
    assert message_has_media(msg) is False


def test_has_media_none_content():
    msg = _mock_message(content=None)
    assert message_has_media(msg) is False


def test_has_media_requires_scheme():
    # "http" without "://" should not match
    msg = _mock_message(content="see http for more info")
    assert message_has_media(msg) is False


def test_has_media_url_case_insensitive():
    msg = _mock_message(content="HTTPS://EXAMPLE.COM")
    assert message_has_media(msg) is True


def test_has_media_forwarded_with_attachment():
    snap = _mock_snapshot(attachments=[Mock()])
    msg = _mock_message(snapshots=[snap])
    assert message_has_media(msg) is True


def test_has_media_forwarded_with_url():
    snap = _mock_snapshot(content="https://example.com/pic.png")
    msg = _mock_message(snapshots=[snap])
    assert message_has_media(msg) is True


def test_has_media_forwarded_with_sticker():
    snap = _mock_snapshot(stickers=[Mock()])
    msg = _mock_message(snapshots=[snap])
    assert message_has_media(msg) is True


def test_has_media_forwarded_text_only():
    snap = _mock_snapshot(content="just text")
    msg = _mock_message(snapshots=[snap])
    assert message_has_media(msg) is False


# ---------------------------------------------------------------------------- #
#                           State persistence                                  #
# ---------------------------------------------------------------------------- #


def test_state_yaml_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(media_state.store, "state_dir", tmp_path)
    media_state.store.cache.clear()

    gs = MediaGuildState(
        guild_id=100,
        guild_name="Test Guild",
        channels=[
            MediaChannelEntry(
                channel_id=200,
                channel_name="memes",
                redirect_channel_id=300,
                redirect_channel_name="general",
                expiry_minutes=1440,
            ),
            MediaChannelEntry(
                channel_id=201,
                channel_name="art",
            ),
        ],
    )
    media_state.save(gs)

    media_state.store.cache.clear()
    loaded = media_state.load(100)

    assert loaded.guild_id == 100
    assert loaded.guild_name == "Test Guild"
    assert len(loaded.channels) == 2

    first = next(c for c in loaded.channels if c.channel_id == 200)
    assert first.channel_name == "memes"
    assert first.redirect_channel_id == 300
    assert first.redirect_channel_name == "general"
    assert first.expiry_minutes == 1440

    second = next(c for c in loaded.channels if c.channel_id == 201)
    assert second.channel_name == "art"
    assert second.redirect_channel_id is None
    assert second.expiry_minutes is None


def test_state_round_trip_no_optionals(tmp_path, monkeypatch):
    monkeypatch.setattr(media_state.store, "state_dir", tmp_path)
    media_state.store.cache.clear()

    gs = MediaGuildState(guild_id=101, guild_name="Empty Guild")
    media_state.save(gs)

    media_state.store.cache.clear()
    loaded = media_state.load(101)
    assert loaded.channels == []


def test_load_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(media_state.store, "state_dir", tmp_path)
    media_state.store.cache.clear()

    loaded = media_state.load(999)
    assert loaded.guild_id == 999
    assert loaded.channels == []


def test_load_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(media_state.store, "state_dir", tmp_path)
    media_state.store.cache.clear()

    gs = MediaGuildState(guild_id=102, guild_name="Cached Guild")
    media_state.save(gs)

    first = media_state.load(102)
    second = media_state.load(102)
    assert first is second  # Same object from cache


# ---------------------------------------------------------------------------- #
#                              Model validation                                #
# ---------------------------------------------------------------------------- #


def test_expiry_minutes_must_be_positive():
    with pytest.raises(Exception):
        MediaChannelEntry(channel_id=1, channel_name="x", expiry_minutes=0)


def test_expiry_minutes_negative_rejected():
    with pytest.raises(Exception):
        MediaChannelEntry(channel_id=1, channel_name="x", expiry_minutes=-10)


def test_channel_name_cannot_be_empty():
    with pytest.raises(Exception):
        MediaChannelEntry(channel_id=1, channel_name="")


# ---------------------------------------------------------------------------- #
#                      Per-guild cron isolation                                #
# ---------------------------------------------------------------------------- #


def _make_cron_bot(guild_ids: list[int]) -> MagicMock:
    bot = MagicMock()
    guilds = {
        gid: SimpleNamespace(id=gid, name=f"G{gid}", owner_id=99) for gid in guild_ids
    }
    bot.cache.get_guilds_view.return_value = guilds
    bot.state.return_value = None
    return bot


async def test_cron_isolates_guild_state_load_failures(monkeypatch):
    """A corrupt state YAML for one guild must not prevent other guilds from being processed."""
    bot = _make_cron_bot([1, 2])
    good_entry = MediaChannelEntry(
        channel_id=300, channel_name="art", expiry_minutes=60
    )

    def fake_load(guild_id: int):
        if guild_id == 1:
            raise RuntimeError("corrupt state YAML")
        return MediaGuildState(guild_id=guild_id, channels=[good_entry])

    monkeypatch.setattr(media_state, "load", fake_load)

    run_cleanup_mock = AsyncMock()
    monkeypatch.setattr(
        "dragonpaw_bot.context.ChannelContext.run_cleanup", run_cleanup_mock
    )

    await media_cron.media_channels_hourly(bot)

    assert run_cleanup_mock.await_count == 1


async def test_cron_isolates_run_cleanup_failures(monkeypatch):
    """A task raising during gather must not prevent other tasks from completing."""
    bot = _make_cron_bot([1, 2])
    entry_a = MediaChannelEntry(channel_id=300, channel_name="art", expiry_minutes=60)
    entry_b = MediaChannelEntry(channel_id=400, channel_name="memes", expiry_minutes=60)

    def fake_load(guild_id: int):
        return MediaGuildState(
            guild_id=guild_id,
            channels=[entry_a if guild_id == 1 else entry_b],
        )

    monkeypatch.setattr(media_state, "load", fake_load)

    async def flaky_run_cleanup(self, expiry_minutes):
        if int(self.channel_id) == 300:
            raise RuntimeError("simulated cleanup error")

    monkeypatch.setattr(
        "dragonpaw_bot.context.ChannelContext.run_cleanup", flaky_run_cleanup
    )

    await media_cron.media_channels_hourly(bot)


async def test_cron_skips_entries_without_expiry(monkeypatch):
    """Media channels without expiry_minutes set must not trigger cleanup."""
    bot = _make_cron_bot([1])
    no_expiry = MediaChannelEntry(channel_id=500, channel_name="memes")

    monkeypatch.setattr(
        media_state,
        "load",
        lambda gid: MediaGuildState(guild_id=gid, channels=[no_expiry]),
    )

    run_cleanup_mock = AsyncMock()
    monkeypatch.setattr(
        "dragonpaw_bot.context.ChannelContext.run_cleanup", run_cleanup_mock
    )

    await media_cron.media_channels_hourly(bot)

    run_cleanup_mock.assert_not_awaited()


# ---------------------------------------------------------------------------- #
#                               on_message                                     #
# ---------------------------------------------------------------------------- #


async def test_on_message_deletes_text_post_and_schedules_notice_cleanup(
    tmp_path, monkeypatch
):
    """Text-only post in a media channel: message deleted, notice posted, and the
    notice's 15s auto-delete actually scheduled and run."""
    monkeypatch.setattr(media_state.store, "state_dir", tmp_path)
    media_state.store.cache.clear()
    media_state.save(
        MediaGuildState(
            guild_id=1,
            guild_name="TestGuild",
            channels=[MediaChannelEntry(channel_id=77, channel_name="pics")],
        )
    )

    msg = _mock_message(content="just some words")
    msg.id = hikari.Snowflake(900)
    msg.author.is_bot = False
    msg.author.id = 42

    event = MagicMock()
    event.message = msg
    event.guild_id = hikari.Snowflake(1)
    event.channel_id = hikari.Snowflake(77)
    event.member.display_name = "Wordy"

    bot = event.app
    bot.state = Mock(return_value=None)  # no log channel configured
    bot.rest.delete_message = AsyncMock()
    notice = Mock()
    notice.id = hikari.Snowflake(901)
    bot.rest.create_message = AsyncMock(return_value=notice)
    guild = Mock()
    guild.id = hikari.Snowflake(1)
    guild.name = "TestGuild"
    bot.cache.get_guild = Mock(return_value=guild)

    scheduled: list[tuple[int, float]] = []

    async def _fake_delete_after(_bot, _channel_id, message_id, delay):
        scheduled.append((int(message_id), delay))

    monkeypatch.setattr(
        "dragonpaw_bot.plugins.media_channels.listeners._delete_after",
        _fake_delete_after,
    )

    await on_message(event)
    await asyncio.sleep(0)  # let the scheduled auto-delete task run

    bot.rest.delete_message.assert_awaited_once_with(channel=77, message=900)
    bot.rest.create_message.assert_awaited_once()
    assert "<@42>" in bot.rest.create_message.call_args.kwargs["content"]
    assert scheduled == [(901, 15.0)]
