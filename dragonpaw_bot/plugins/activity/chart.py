"""Activity bar chart: stacked daily contributions by kind."""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import hikari
from PIL import Image, ImageDraw, ImageFont

from dragonpaw_bot.plugins.activity.models import ContributionBucket, ContributionKind

FONTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fonts"
FONT_BOLD = FONTS_DIR / "DaxCondensed-Bold.ttf"
FONT_REGULAR = FONTS_DIR / "DaxCondensed-Regular.ttf"

# Canvas
CANVAS_W = 640
CANVAS_H = 280
PADDING = 20
MARGIN_LEFT = 52  # y-axis labels
MARGIN_BOTTOM = 36  # x-axis labels
MARGIN_TOP = 44  # title row
CORNER_RADIUS = 10

CHART_X = PADDING + MARGIN_LEFT
CHART_Y = PADDING + MARGIN_TOP
CHART_W = CANVAS_W - PADDING - MARGIN_LEFT - PADDING
CHART_H = CANVAS_H - PADDING - MARGIN_TOP - MARGIN_BOTTOM - PADDING

MAX_DAYS = 60

BG_COLOR = (252, 248, 240)
TEXT_COLOR = (60, 60, 60)
AXIS_COLOR = (200, 195, 185)
GRIDLINE_COLOR = (235, 230, 222)

KIND_COLORS: dict[ContributionKind, tuple[int, int, int]] = {
    ContributionKind.TEXT: (100, 180, 120),  # sage green
    ContributionKind.MEDIA: (80, 160, 210),  # sky blue
    ContributionKind.REACTION: (240, 190, 60),  # warm amber
    ContributionKind.VC: (180, 120, 200),  # soft purple
}
KIND_LABELS = {
    ContributionKind.TEXT: "Text",
    ContributionKind.MEDIA: "Media",
    ContributionKind.REACTION: "React",
    ContributionKind.VC: "VC",
}
KIND_ORDER = [
    ContributionKind.TEXT,
    ContributionKind.MEDIA,
    ContributionKind.REACTION,
    ContributionKind.VC,
]

BAR_GAP = 2


def _day_key(ts: float) -> int:
    """Floor a unix timestamp to the start of its UTC day."""
    return int(ts) - (int(ts) % 86400)


def _apply_rounded_corners(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [(0, 0), (img.width - 1, img.height - 1)], radius=radius, fill=255
    )
    img.putalpha(mask)
    return img


def _nice_max(value: float) -> float:
    """Round value up to a visually clean y-axis ceiling."""
    for step in (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000):
        if step >= value:
            return float(step)
    return value


def _build_daily(
    buckets: list[ContributionBucket],
) -> tuple[list[int], dict[int, dict[ContributionKind, float]], float]:
    """Aggregate buckets into daily totals.

    Returns (days, daily, nice_max) where days is a contiguous list of day
    timestamps from first bucket (capped at MAX_DAYS ago) through today.
    """
    now_ts = datetime.now(UTC).timestamp()
    cutoff = _day_key(now_ts - MAX_DAYS * 86400)
    today = _day_key(now_ts)

    daily: dict[int, dict[ContributionKind, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for b in buckets:
        day = _day_key(b.hour)
        if day >= cutoff:
            daily[day][b.kind] += b.amount

    first_day = max(min(daily), cutoff) if daily else today
    days: list[int] = []
    d = first_day
    while d <= today:
        days.append(d)
        d += 86400

    max_total = max(
        (sum(daily[day].values()) for day in days if day in daily),
        default=1.0,
    )
    return days, daily, _nice_max(max(1.0, max_total))


def _draw_title(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    username: str,
    score: float,
    status_emoji: str,
) -> None:
    draw.text(
        (CHART_X, PADDING + 4), f"Activity — {username}", font=font, fill=TEXT_COLOR
    )
    score_text = f"Score: {score:.2f} {status_emoji}"
    sb = font.getbbox(score_text)
    draw.text(
        (CANVAS_W - PADDING - (sb[2] - sb[0]), PADDING + 4),
        score_text,
        font=font,
        fill=TEXT_COLOR,
    )


def _draw_gridlines(
    draw: ImageDraw.ImageDraw,
    label_font: ImageFont.FreeTypeFont,
    nice_max: float,
) -> None:
    for frac, label in (
        (1.0, f"{nice_max:.4g}"),
        (0.5, f"{nice_max / 2:.4g}"),
        (0.0, "0"),
    ):
        y = int(CHART_Y + CHART_H * (1.0 - frac))
        draw.line([(CHART_X, y), (CHART_X + CHART_W, y)], fill=GRIDLINE_COLOR, width=1)
        lb = label_font.getbbox(label)
        draw.text(
            (CHART_X - (lb[2] - lb[0]) - 6, y - (lb[3] - lb[1]) // 2),
            label,
            font=label_font,
            fill=TEXT_COLOR,
        )


def _draw_bars(
    draw: ImageDraw.ImageDraw,
    days: list[int],
    daily: dict[int, dict[ContributionKind, float]],
    nice_max: float,
) -> float:
    """Draw stacked bars. Returns bar_slot width (used by x-label drawing)."""
    num_days = len(days)
    if num_days == 0:
        return 0.0
    bar_slot = CHART_W / num_days
    bar_w = max(1, bar_slot - BAR_GAP)
    for i, day in enumerate(days):
        bar_left = CHART_X + i * bar_slot + BAR_GAP / 2
        bottom = float(CHART_Y + CHART_H)
        for kind in KIND_ORDER:
            amount = daily.get(day, {}).get(kind, 0.0)
            if amount <= 0:
                continue
            bar_h = max(1, int(CHART_H * amount / nice_max))
            top = bottom - bar_h
            draw.rectangle(
                [int(bar_left), int(top), int(bar_left + bar_w), int(bottom)],
                fill=(*KIND_COLORS[kind], 255),
            )
            bottom = top
    return bar_slot


def _draw_x_labels(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    days: list[int],
    bar_slot: float,
) -> None:
    num_days = len(days)
    label_step = max(7, num_days // 8)
    for i, day in enumerate(days):
        if i % label_step != 0 and i != num_days - 1:
            continue
        x = int(CHART_X + i * bar_slot + bar_slot / 2)
        label = datetime.fromtimestamp(day, tz=UTC).strftime("%-d %b")
        lb = font.getbbox(label)
        draw.text(
            (x - (lb[2] - lb[0]) // 2, CHART_Y + CHART_H + 6),
            label,
            font=font,
            fill=TEXT_COLOR,
        )


def _draw_legend(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
) -> None:
    swatch, gap = 10, 6
    legend_y = CHART_Y + CHART_H + MARGIN_BOTTOM - 14
    legend_x = CHART_X + CHART_W
    for kind in reversed(KIND_ORDER):
        label = KIND_LABELS[kind]
        lw = font.getbbox(label)[2] - font.getbbox(label)[0]
        legend_x -= lw + gap
        draw.text((legend_x, legend_y), label, font=font, fill=TEXT_COLOR)
        legend_x -= swatch + gap
        draw.rectangle(
            [legend_x, legend_y + 1, legend_x + swatch, legend_y + swatch],
            fill=(*KIND_COLORS[kind], 255),
        )
        legend_x -= gap


def render_activity_chart(
    username: str,
    buckets: list[ContributionBucket],
    score: float,
    status_emoji: str,
) -> hikari.Bytes:
    """Render a stacked daily-contribution bar chart and return as a PNG attachment."""
    days, daily, nice_max = _build_daily(buckets)

    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), (*BG_COLOR, 255))
    draw = ImageDraw.Draw(img)
    title_font = ImageFont.truetype(str(FONT_BOLD), 20)
    label_font = ImageFont.truetype(str(FONT_REGULAR), 13)
    small_font = ImageFont.truetype(str(FONT_REGULAR), 11)

    _draw_title(draw, title_font, username, score, status_emoji)
    _draw_gridlines(draw, label_font, nice_max)
    bar_slot = _draw_bars(draw, days, daily, nice_max)
    if bar_slot > 0:
        _draw_x_labels(draw, small_font, days, bar_slot)
    _draw_legend(draw, small_font)

    # Axis lines
    draw.line(
        [(CHART_X, CHART_Y), (CHART_X, CHART_Y + CHART_H)], fill=AXIS_COLOR, width=1
    )
    draw.line(
        [(CHART_X, CHART_Y + CHART_H), (CHART_X + CHART_W, CHART_Y + CHART_H)],
        fill=AXIS_COLOR,
        width=1,
    )

    img = _apply_rounded_corners(img, CORNER_RADIUS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return hikari.Bytes(buf, "activity_chart.png")
