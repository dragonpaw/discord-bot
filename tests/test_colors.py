import hikari

from dragonpaw_bot.colors import (
    SOLARIZED_BLUE,
    SOLARIZED_GREEN,
    SOLARIZED_RED,
    rainbow,
)


def test_rainbow_length():
    for n in (3, 5, 10):
        colors = rainbow(n)
        assert len(colors) == n


def test_rainbow_values_in_range():
    colors = rainbow(5)
    for r, g, b in colors:
        assert 0 <= r <= 255
        assert 0 <= g <= 255
        assert 0 <= b <= 255


def test_solarized_constants_are_colors():
    assert isinstance(SOLARIZED_BLUE, hikari.Color)
    assert isinstance(SOLARIZED_RED, hikari.Color)
    assert isinstance(SOLARIZED_GREEN, hikari.Color)
