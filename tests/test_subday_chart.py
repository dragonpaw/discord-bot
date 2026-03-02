from __future__ import annotations

import hikari
from PIL import Image

from dragonpaw_bot.plugins.subday.chart import render_star_chart


def _to_image(result: hikari.Bytes) -> Image.Image:
    """Convert hikari.Bytes to a PIL Image for inspection."""
    import io

    return Image.open(io.BytesIO(result.data))


def test_returns_hikari_bytes():
    result = render_star_chart("TestUser", 1, False)
    assert isinstance(result, hikari.Bytes)
    assert result.filename == "star_chart.png"


def test_returns_valid_png():
    result = render_star_chart("TestUser", 10, False)
    img = _to_image(result)
    assert img.format == "PNG"
    assert img.width > 0
    assert img.height > 0


def test_week_1_not_completed():
    result = render_star_chart("TestUser", 1, False)
    assert len(result.data) > 0


def test_milestone_week_13():
    result = render_star_chart("TestUser", 13, True)
    assert len(result.data) > 0


def test_milestone_week_26():
    result = render_star_chart("TestUser", 26, True)
    assert len(result.data) > 0


def test_graduated():
    result = render_star_chart("TestUser", 52, True)
    assert len(result.data) > 0


def test_post_graduation():
    """Week 53 means graduated — all stars should be filled."""
    result = render_star_chart("TestUser", 53, False)
    assert len(result.data) > 0


def test_different_users_get_different_colors():
    """Different usernames should produce different images."""
    r1 = render_star_chart("Alice", 10, True)
    r2 = render_star_chart("Bob", 10, True)
    assert r1.data != r2.data


def test_same_user_is_deterministic():
    """Same inputs should always produce the same image."""
    r1 = render_star_chart("Alice", 10, True)
    r2 = render_star_chart("Alice", 10, True)
    assert r1.data == r2.data
