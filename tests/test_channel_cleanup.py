import pytest

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
            CleanupChannelEntry(channel_id=300, channel_name="venting", expiry_minutes=1440),
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
