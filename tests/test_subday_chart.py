from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

import hikari
from PIL import Image

from dragonpaw_bot.plugins.subday.chart import (
    PALETTES,
    _grid_cell,
    render_star_chart,
)
from dragonpaw_bot.plugins.subday.models import StarPalette, SubDayParticipant


def _to_image(result: hikari.Bytes) -> Image.Image:
    """Convert hikari.Bytes to a PIL Image for inspection."""
    return Image.open(io.BytesIO(result.data))


def test_returns_hikari_bytes():
    result = render_star_chart("TestUser", 1, False, StarPalette.RANDOM)
    assert isinstance(result, hikari.Bytes)
    assert result.filename == "star_chart.png"


def test_returns_valid_png():
    result = render_star_chart("TestUser", 10, False, StarPalette.RANDOM)
    img = _to_image(result)
    assert img.format == "PNG"
    assert img.width > 0
    assert img.height > 0


def test_week_1_not_completed():
    result = render_star_chart("TestUser", 1, False, StarPalette.RANDOM)
    assert len(result.data) > 0


def test_milestone_week_13():
    result = render_star_chart("TestUser", 13, True, StarPalette.RANDOM)
    assert len(result.data) > 0


def test_milestone_week_26():
    result = render_star_chart("TestUser", 26, True, StarPalette.RANDOM)
    assert len(result.data) > 0


def test_graduated():
    """Week 52 completed means graduated — the full chart still renders."""
    result = render_star_chart("TestUser", 52, True, StarPalette.RANDOM)
    assert len(result.data) > 0


def test_different_users_get_different_colors():
    """Different usernames should produce different images."""
    r1 = render_star_chart("Alice", 10, True, StarPalette.RANDOM)
    r2 = render_star_chart("Bob", 10, True, StarPalette.RANDOM)
    assert r1.data != r2.data


def test_same_user_is_deterministic():
    """Same inputs should always produce the same image."""
    r1 = render_star_chart("Alice", 10, True, StarPalette.RANDOM)
    r2 = render_star_chart("Alice", 10, True, StarPalette.RANDOM)
    assert r1.data == r2.data


def test_chart_stable_across_processes():
    """Star colors are seeded by username and documented as consistent across
    renders — the seed must survive a bot restart (hash() is salted per process)."""
    script = (
        "from dragonpaw_bot.plugins.subday import chart; import sys; "
        "b = chart.render_star_chart(username='Testy', current_week=5, "
        "week_completed=True, palette=chart.StarPalette.RAINBOW); sys.stdout.buffer.write(bytes(b.data))"
    )
    digests = []
    for seed in ("1", "2"):
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        digests.append(hashlib.sha256(proc.stdout).hexdigest())
    assert digests[0] == digests[1]


def test_every_palette_renders_differently():
    charts = {
        render_star_chart("Alice", 52, True, palette).data for palette in StarPalette
    }
    assert len(charts) == len(StarPalette)


def test_grid_cell_skips_prize_cells():
    assert _grid_cell(1) == 0
    assert _grid_cell(13) == 12
    assert _grid_cell(14) == 14  # cell 13 is section 1's prize
    assert _grid_cell(52) == 54


def test_seven_colour_palette_lines_up_with_columns():
    """Weeks 1, 8, 14 and 21 all sit in the first column of the grid."""
    assert {_grid_cell(week) % 7 for week in (1, 8, 14, 21)} == {0}


def test_flag_designs_cover_whole_grid():
    """Flag designs are drawn one colour per cell, so a short one would wrap."""
    for palette in (StarPalette.TRANS, StarPalette.LEATHER):
        assert len(PALETTES[palette].stars or ()) == 56


def test_participant_palette_defaults_to_random():
    p = SubDayParticipant.model_validate(
        {"user_id": 1, "signup_date": "2026-01-01T00:00:00Z"}
    )
    assert p.palette is StarPalette.RANDOM
    dumped = p.model_dump(mode="json")
    assert dumped["palette"] == "random"
    assert SubDayParticipant.model_validate(dumped).palette is StarPalette.RANDOM
