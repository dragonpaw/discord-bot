from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from dragonpaw_bot.plugins.channel_cleanup import cron as cleanup_cron
from dragonpaw_bot.plugins.channel_cleanup import state as cleanup_state
from dragonpaw_bot.plugins.channel_cleanup.models import (
    CleanupChannelEntry,
    CleanupGuildState,
)

# ---------------------------------------------------------------------------- #
#                           State persistence                                  #
# ---------------------------------------------------------------------------- #


def test_state_yaml_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup_state, "STATE_DIR", tmp_path)
    cleanup_state._cache.clear()

    gs = CleanupGuildState(
        guild_id=200,
        guild_name="Cleanup Guild",
        channels=[
            CleanupChannelEntry(
                channel_id=300, channel_name="venting", expiry_minutes=1440
            ),
            CleanupChannelEntry(channel_id=301, channel_name="spam", expiry_minutes=60),
        ],
    )
    cleanup_state.save(gs)

    cleanup_state._cache.clear()
    loaded = cleanup_state.load(200)

    assert loaded.guild_id == 200
    assert loaded.guild_name == "Cleanup Guild"
    assert len(loaded.channels) == 2

    venting = next(c for c in loaded.channels if c.channel_id == 300)
    assert venting.channel_name == "venting"
    assert venting.expiry_minutes == 1440

    spam = next(c for c in loaded.channels if c.channel_id == 301)
    assert spam.expiry_minutes == 60


def test_state_round_trip_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup_state, "STATE_DIR", tmp_path)
    cleanup_state._cache.clear()

    gs = CleanupGuildState(guild_id=201, guild_name="Empty")
    cleanup_state.save(gs)

    cleanup_state._cache.clear()
    loaded = cleanup_state.load(201)
    assert loaded.channels == []


def test_load_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup_state, "STATE_DIR", tmp_path)
    cleanup_state._cache.clear()

    loaded = cleanup_state.load(999)
    assert loaded.guild_id == 999
    assert loaded.channels == []


def test_load_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup_state, "STATE_DIR", tmp_path)
    cleanup_state._cache.clear()

    gs = CleanupGuildState(guild_id=202, guild_name="Cached")
    cleanup_state.save(gs)

    first = cleanup_state.load(202)
    second = cleanup_state.load(202)
    assert first is second


# ---------------------------------------------------------------------------- #
#                              Model validation                                #
# ---------------------------------------------------------------------------- #


def test_expiry_minutes_must_be_positive():
    with pytest.raises(Exception):
        CleanupChannelEntry(channel_id=1, channel_name="x", expiry_minutes=0)


def test_expiry_minutes_negative_rejected():
    with pytest.raises(Exception):
        CleanupChannelEntry(channel_id=1, channel_name="x", expiry_minutes=-5)


def test_channel_name_cannot_be_empty():
    with pytest.raises(Exception):
        CleanupChannelEntry(channel_id=1, channel_name="", expiry_minutes=60)


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
    good_entry = CleanupChannelEntry(
        channel_id=300, channel_name="venting", expiry_minutes=60
    )

    def fake_load(guild_id: int):
        if guild_id == 1:
            raise RuntimeError("corrupt state YAML")
        return CleanupGuildState(guild_id=guild_id, channels=[good_entry])

    monkeypatch.setattr(cleanup_state, "load", fake_load)

    run_cleanup_mock = AsyncMock()
    monkeypatch.setattr(
        "dragonpaw_bot.context.ChannelContext.run_cleanup", run_cleanup_mock
    )

    await cleanup_cron.channel_cleanup_hourly(bot)

    # Guild 2's channel must still have been cleaned despite guild 1 blowing up
    assert run_cleanup_mock.await_count == 1


async def test_cron_isolates_run_cleanup_failures(monkeypatch):
    """A task raising during gather must not prevent other tasks from completing."""
    bot = _make_cron_bot([1, 2])
    entry_a = CleanupChannelEntry(
        channel_id=300, channel_name="venting", expiry_minutes=60
    )
    entry_b = CleanupChannelEntry(
        channel_id=400, channel_name="spam", expiry_minutes=60
    )

    def fake_load(guild_id: int):
        return CleanupGuildState(
            guild_id=guild_id,
            channels=[entry_a if guild_id == 1 else entry_b],
        )

    monkeypatch.setattr(cleanup_state, "load", fake_load)

    async def flaky_run_cleanup(self, expiry_minutes):
        if int(self.channel_id) == 300:
            raise RuntimeError("simulated cleanup error")

    monkeypatch.setattr(
        "dragonpaw_bot.context.ChannelContext.run_cleanup", flaky_run_cleanup
    )

    # Cron must not raise — _safe_run_cleanup catches per-task failures
    await cleanup_cron.channel_cleanup_hourly(bot)
