from unittest.mock import Mock

import hikari
import pytest

from dragonpaw_bot.plugins.media_channels import _has_media
from dragonpaw_bot.plugins.media_channels import state as media_state
from dragonpaw_bot.plugins.media_channels.models import (
    MediaChannelEntry,
    MediaGuildState,
)

# ---------------------------------------------------------------------------- #
#                               _has_media                                     #
# ---------------------------------------------------------------------------- #


def _mock_message(
    content: str | None = None,
    attachments: list | None = None,
    stickers: list | None = None,
) -> Mock:
    msg = Mock(spec=hikari.Message)
    msg.content = content
    msg.attachments = attachments or []
    msg.stickers = stickers or []
    return msg


def test_has_media_with_attachment():
    msg = _mock_message(content=None, attachments=[Mock()])
    assert _has_media(msg) is True


def test_has_media_with_https_url():
    msg = _mock_message(content="check this out https://example.com")
    assert _has_media(msg) is True


def test_has_media_with_http_url():
    msg = _mock_message(content="http://example.com/image.png")
    assert _has_media(msg) is True


def test_has_media_with_sticker():
    msg = _mock_message(stickers=[Mock()])
    assert _has_media(msg) is True


def test_has_media_plain_text():
    msg = _mock_message(content="just some text")
    assert _has_media(msg) is False


def test_has_media_none_content():
    msg = _mock_message(content=None)
    assert _has_media(msg) is False


def test_has_media_requires_scheme():
    # "http" without "://" should not match
    msg = _mock_message(content="see http for more info")
    assert _has_media(msg) is False


def test_has_media_url_case_insensitive():
    msg = _mock_message(content="HTTPS://EXAMPLE.COM")
    assert _has_media(msg) is True


# ---------------------------------------------------------------------------- #
#                           State persistence                                  #
# ---------------------------------------------------------------------------- #


def test_state_yaml_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(media_state, "STATE_DIR", tmp_path)
    media_state._cache.clear()

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

    media_state._cache.clear()
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
    monkeypatch.setattr(media_state, "STATE_DIR", tmp_path)
    media_state._cache.clear()

    gs = MediaGuildState(guild_id=101, guild_name="Empty Guild")
    media_state.save(gs)

    media_state._cache.clear()
    loaded = media_state.load(101)
    assert loaded.channels == []


def test_load_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(media_state, "STATE_DIR", tmp_path)
    media_state._cache.clear()

    loaded = media_state.load(999)
    assert loaded.guild_id == 999
    assert loaded.channels == []


def test_load_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(media_state, "STATE_DIR", tmp_path)
    media_state._cache.clear()

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
