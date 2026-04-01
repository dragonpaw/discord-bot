from __future__ import annotations

import io
import math
import random
from pathlib import Path

import hikari
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from dragonpaw_bot.plugins.subday.constants import TOTAL_WEEKS

# ---------------------------------------------------------------------------- #
#                                   Constants                                   #
# ---------------------------------------------------------------------------- #

FONTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fonts"
FONT_TITLE = FONTS_DIR / "DaxCondensed-Bold.ttf"
FONT_NUMBERS = FONTS_DIR / "DaxCondensed-Regular.ttf"
FONT_NUMBERS_LIGHT = FONTS_DIR / "DaxCondensed_Light.ttf"
FONT_MILESTONE = FONTS_DIR / "DaxCondensed-Medium.ttf"
FONT_USERNAME = FONTS_DIR / "Caveat-Bold.ttf"

COLS = 7
ROWS_PER_SECTION = 2
CELLS_PER_SECTION = COLS * ROWS_PER_SECTION  # 14
WEEKS_PER_SECTION = CELLS_PER_SECTION - 1  # 13 (last cell is the prize cell)
SECTIONS = 4

# Supersampling scale for anti-aliased stars
SS = 3

# Canvas
PADDING = 24
TITLE_HEIGHT = 62  # includes progress area
PROGRESS_AREA_HEIGHT = 14
SECTION_GAP = 16
CELL_W = 100
CELL_H = 44
CORNER_RADIUS = 10

GRID_WIDTH = COLS * CELL_W
CANVAS_WIDTH = GRID_WIDTH + 2 * PADDING
GRID_HEIGHT = SECTIONS * ROWS_PER_SECTION * CELL_H + (SECTIONS - 1) * SECTION_GAP
CANVAS_HEIGHT = PADDING + TITLE_HEIGHT + GRID_HEIGHT + PADDING

# Star geometry — golden-ratio-based inner radius for a classic star shape
STAR_OUTER = 22
STAR_INNER = STAR_OUTER * 0.38
GOLD_STAR_OUTER = 26
GOLD_STAR_INNER = GOLD_STAR_OUTER * 0.38

# Colors
BG_COLOR = (252, 248, 240)
TEXT_COLOR = (60, 60, 60)
NUMBER_COLOR = (185, 180, 170)
EMPTY_STAR_COLOR = (225, 220, 212)
GOLD = (255, 215, 0)
GOLD_DARK = (200, 160, 0)
GOLD_GLOW = (255, 230, 80, 90)
HOT_PINK = (255, 20, 100)

STICKER_COLORS = [
    (200, 60, 60),  # muted red
    (225, 95, 135),  # dusty rose
    (75, 110, 200),  # medium blue
    (100, 165, 210),  # soft sky
    (70, 155, 90),  # sage green
    (160, 195, 70),  # olive-lime
    (230, 185, 55),  # warm amber
    (215, 125, 50),  # burnt orange
    (130, 75, 175),  # muted purple
    (50, 165, 155),  # soft teal
    (175, 70, 145),  # plum
    (85, 180, 130),  # seafoam
]

# Section background tints (progressively warmer)
SECTION_TINTS: list[tuple[int, int, int, int] | None] = [
    None,
    (250, 244, 232, 25),
    (248, 238, 222, 35),
    (245, 232, 210, 45),
]

# Progress bar colors
PROGRESS_TRACK_COLOR = (235, 230, 222)
PROGRESS_FILL_COLOR = (200, 180, 140)
PROGRESS_TEXT_COLOR = (160, 155, 145)

# Milestone cell highlight colors
MILESTONE_HIGHLIGHT_ACTIVE = (255, 245, 210, 60)
MILESTONE_HIGHLIGHT_INACTIVE = (240, 238, 232, 40)

# Icon color for prize line drawings
ICON_COLOR = (100, 100, 100)
ICON_COLOR_ACTIVE = (180, 140, 30)


# ---------------------------------------------------------------------------- #
#                               Star rendering                                  #
# ---------------------------------------------------------------------------- #


def _star_points(
    cx: float,
    cy: float,
    outer_r: float,
    inner_r: float,
    rotation: float = 0.0,
    points: int = 5,
) -> list[tuple[float, float]]:
    """Compute vertices of a star polygon."""
    verts: list[tuple[float, float]] = []
    angle_step = math.pi / points
    start = -math.pi / 2 + rotation
    for i in range(points * 2):
        r = outer_r if i % 2 == 0 else inner_r
        angle = start + i * angle_step
        verts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return verts


def _render_star_sprite(
    size: int,
    fill: tuple[int, int, int] | None,
    outline: tuple[int, int, int],
    outer_r: float,
    inner_r: float,
    rotation: float = 0.0,
    outline_width: int = 1,
) -> Image.Image:
    """Render a single anti-aliased star as an RGBA sprite.

    Draws at SS× resolution then downscales with LANCZOS for smooth edges.
    """
    big = size * SS
    cx = cy = big // 2
    sprite = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sprite)
    pts = _star_points(cx, cy, outer_r * SS, inner_r * SS, rotation)
    if fill:
        draw.polygon(pts, fill=(*fill, 255))
        draw.polygon(pts, fill=None, outline=(*outline, 255), width=2 * SS)
    else:
        draw.polygon(pts, fill=None, outline=(*outline, 255), width=outline_width * SS)
    return sprite.resize((size, size), Image.Resampling.LANCZOS)


def _paste_star(
    img: Image.Image,
    cx: float,
    cy: float,
    fill: tuple[int, int, int] | None,
    outline: tuple[int, int, int],
    outer_r: float,
    inner_r: float,
    rotation: float = 0.0,
    outline_width: int = 1,
) -> None:
    """Paste an anti-aliased star sprite centered at (cx, cy)."""
    size = int(outer_r * 2 + 4)
    sprite = _render_star_sprite(
        size, fill, outline, outer_r, inner_r, rotation, outline_width
    )
    x = int(cx - size / 2)
    y = int(cy - size / 2)
    img.paste(sprite, (x, y), sprite)


def _paste_gold_star_with_glow(
    img: Image.Image,
    cx: float,
    cy: float,
    outer_r: float,
    inner_r: float,
) -> None:
    """Paste a gold star with a soft glow behind it."""
    glow_size = int(outer_r * 3)
    glow_img = Image.new("RGBA", (glow_size, glow_size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_img)
    gcx = gcy = glow_size // 2
    pts = _star_points(gcx, gcy, outer_r * 1.15, inner_r * 1.15)
    glow_draw.polygon(pts, fill=GOLD_GLOW)
    glow_img = glow_img.filter(ImageFilter.GaussianBlur(radius=5))
    gx = int(cx - glow_size / 2)
    gy = int(cy - glow_size / 2)
    img.paste(glow_img, (gx, gy), glow_img)

    _paste_star(
        img, cx, cy, fill=GOLD, outline=GOLD_DARK, outer_r=outer_r, inner_r=inner_r
    )


# ---------------------------------------------------------------------------- #
#                             Prize icon loading                                #
# ---------------------------------------------------------------------------- #

ICONS_DIR = Path(__file__).resolve().parent / "icons"
_ICON_FILES = ["gift_card.png", "tail.png", "butt_plug.png", "flogger.png"]
ICON_SIZE = 34

# Module-level cache for loaded + resized icon sprites
_icon_cache: dict[str, Image.Image] = {}


def _load_icon(filename: str) -> Image.Image:
    """Load a prize icon PNG, resize to ICON_SIZE, and cache it."""
    if filename not in _icon_cache:
        icon = Image.open(ICONS_DIR / filename).convert("RGBA")
        icon.thumbnail((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)
        _icon_cache[filename] = icon
    return _icon_cache[filename]


def _tint_icon(icon: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """Tint an icon to the given color, preserving its alpha channel."""
    rgba = icon.convert("RGBA")
    solid = Image.new("RGBA", rgba.size, (*color, 255))
    # Use the original alpha as the mask for the solid color
    solid.putalpha(rgba.getchannel("A"))
    return solid


def _paste_prize_icon(
    img: Image.Image,
    cx: float,
    cy: float,
    section: int,
    active: bool,
) -> None:
    """Paste a pre-rendered prize icon centered at (cx, cy)."""
    icon = _load_icon(_ICON_FILES[section])
    color = ICON_COLOR_ACTIVE if active else ICON_COLOR
    tinted = _tint_icon(icon, color)
    x = int(cx - tinted.width / 2)
    y = int(cy - tinted.height / 2)
    img.paste(tinted, (x, y), tinted)


# ---------------------------------------------------------------------------- #
#                             Rounded corners                                   #
# ---------------------------------------------------------------------------- #


def _apply_rounded_corners(img: Image.Image, radius: int) -> Image.Image:
    """Apply rounded corners to an RGBA image using an alpha mask."""
    mask = Image.new("L", img.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        [(0, 0), (img.width - 1, img.height - 1)],
        radius=radius,
        fill=255,
    )
    img.putalpha(mask)
    return img


# ---------------------------------------------------------------------------- #
#                             Main render function                              #
# ---------------------------------------------------------------------------- #


def _draw_progress_bar(
    draw: ImageDraw.ImageDraw,
    grid_top: int,
    last_completed: int,
    font: ImageFont.FreeTypeFont,
) -> None:
    """Draw the thin progress bar and fraction text below the title."""
    bar_y = grid_top - PROGRESS_AREA_HEIGHT - 4
    bar_height = 4
    bar_left = PADDING
    bar_right = CANVAS_WIDTH - PADDING
    bar_width = bar_right - bar_left
    progress_frac = last_completed / TOTAL_WEEKS

    # Progress text right-aligned above bar
    progress_text = f"{last_completed} / {TOTAL_WEEKS}"
    pt_bbox = font.getbbox(progress_text)
    pt_w = pt_bbox[2] - pt_bbox[0]
    draw.text(
        (bar_right - pt_w, bar_y - 12),
        progress_text,
        font=font,
        fill=PROGRESS_TEXT_COLOR,
    )

    # Track (rounded)
    draw.rounded_rectangle(
        [(bar_left, bar_y), (bar_right, bar_y + bar_height)],
        radius=2,
        fill=PROGRESS_TRACK_COLOR,
    )
    # Fill (rounded)
    if progress_frac > 0:
        fill_right = bar_left + int(bar_width * progress_frac)
        draw.rounded_rectangle(
            [(bar_left, bar_y), (fill_right, bar_y + bar_height)],
            radius=2,
            fill=PROGRESS_FILL_COLOR,
        )

    # Milestone markers at 13, 26, 39, 52
    bar_cy = bar_y + bar_height / 2
    for mw in (13, 26, 39, 52):
        mx = bar_left + int(bar_width * mw / TOTAL_WEEKS)
        reached = last_completed >= mw
        color = PROGRESS_FILL_COLOR if reached else PROGRESS_TRACK_COLOR
        r = 3
        draw.regular_polygon((mx, bar_cy, r), n_sides=4, fill=color, outline=BG_COLOR)


def _draw_section_tints(img: Image.Image, grid_top: int) -> None:
    """Apply progressively warmer tints to each section background."""
    for section in range(SECTIONS):
        tint = SECTION_TINTS[section]
        if tint is None:
            continue
        section_y = grid_top + section * (ROWS_PER_SECTION * CELL_H + SECTION_GAP)
        section_h = ROWS_PER_SECTION * CELL_H
        overlay = Image.new("RGBA", (GRID_WIDTH, section_h), tint)
        img.paste(
            Image.alpha_composite(
                img.crop(
                    (PADDING, section_y, PADDING + GRID_WIDTH, section_y + section_h)
                ),
                overlay,
            ),
            (PADDING, section_y),
        )


def render_star_chart(
    username: str,
    current_week: int,
    week_completed: bool,
) -> hikari.Bytes:
    """Generate a star chart image and return as a hikari attachment.

    Args:
        username: Display name for the chart title.
        current_week: The participant's current week (1-52, or 53 if graduated).
        week_completed: Whether the current week has been completed.
    """
    rng = random.Random(hash(username))

    # Pre-assign a random color and rotation to each week for consistency
    week_colors = [rng.choice(STICKER_COLORS) for _ in range(TOTAL_WEEKS)]
    week_rotations = [rng.uniform(-0.20, 0.20) for _ in range(TOTAL_WEEKS)]
    week_jitter_x = [rng.uniform(-3, 3) for _ in range(TOTAL_WEEKS)]
    week_jitter_y = [rng.uniform(-1, 1) for _ in range(TOTAL_WEEKS)]

    img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (*BG_COLOR, 255))
    draw = ImageDraw.Draw(img)

    # Load fonts
    title_font = ImageFont.truetype(str(FONT_TITLE), 32)
    username_font = ImageFont.truetype(str(FONT_USERNAME), 38)
    number_font = ImageFont.truetype(str(FONT_NUMBERS_LIGHT), 13)
    progress_font = ImageFont.truetype(str(FONT_NUMBERS_LIGHT), 11)

    # ---- Title bar ----
    title_text = "Subday Journals:"
    draw.text((PADDING, PADDING - 4), title_text, font=title_font, fill=TEXT_COLOR)
    title_w = title_font.getbbox(title_text)[2]
    draw.text(
        (PADDING + title_w + 12, PADDING - 12),
        username,
        font=username_font,
        fill=HOT_PINK,
    )

    # ---- Determine completion ----
    if current_week > TOTAL_WEEKS:
        last_completed = TOTAL_WEEKS
    elif week_completed:
        last_completed = current_week
    else:
        last_completed = current_week - 1

    grid_top = PADDING + TITLE_HEIGHT

    _draw_progress_bar(draw, grid_top, last_completed, progress_font)
    _draw_section_tints(img, grid_top)

    # ---- Grid ----
    week_num = 1

    for section in range(SECTIONS):
        section_y = grid_top + section * (ROWS_PER_SECTION * CELL_H + SECTION_GAP)

        for row in range(ROWS_PER_SECTION):
            for col in range(COLS):
                cell_idx = row * COLS + col
                cx = PADDING + col * CELL_W + CELL_W // 2
                cy = section_y + row * CELL_H + CELL_H // 2

                if cell_idx < WEEKS_PER_SECTION:
                    _draw_week_cell(
                        draw,
                        img,
                        cx,
                        cy,
                        week_num,
                        last_completed,
                        current_week,
                        week_completed,
                        week_colors,
                        week_rotations,
                        week_jitter_x,
                        week_jitter_y,
                        number_font,
                    )
                    week_num += 1
                elif cell_idx == WEEKS_PER_SECTION:
                    milestone_week = (section + 1) * WEEKS_PER_SECTION
                    milestone_reached = last_completed >= milestone_week
                    _draw_prize_cell(
                        draw,
                        img,
                        cx,
                        cy,
                        section,
                        milestone_reached,
                    )

    # ---- Rounded corners & export as RGBA PNG ----
    img = _apply_rounded_corners(img, CORNER_RADIUS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return hikari.Bytes(buf, "star_chart.png")


def _draw_week_cell(
    draw: ImageDraw.ImageDraw,
    img: Image.Image,
    cx: float,
    cy: float,
    week: int,
    last_completed: int,
    current_week: int,
    week_completed: bool,
    colors: list[tuple[int, int, int]],
    rotations: list[float],
    jitter_x: list[float],
    jitter_y: list[float],
    number_font: ImageFont.FreeTypeFont,
) -> None:
    """Draw a single week cell with number and star."""
    idx = week - 1

    # Week number to the left of the star
    num_text = str(week)
    nb = number_font.getbbox(num_text)
    nw = nb[2] - nb[0]
    draw.text(
        (cx - 32 - nw / 2, cy - (nb[3] - nb[1]) / 2 - 1),
        num_text,
        font=number_font,
        fill=NUMBER_COLOR,
    )

    # Star position (with jitter for completed)
    star_cx = cx + 10
    star_cy = cy

    if week <= last_completed:
        _paste_star(
            img,
            star_cx + jitter_x[idx],
            star_cy + jitter_y[idx],
            fill=colors[idx],
            outline=_darken(colors[idx], 0.7),
            outer_r=STAR_OUTER,
            inner_r=STAR_INNER,
            rotation=rotations[idx],
        )
    else:
        _paste_star(
            img,
            star_cx,
            star_cy,
            fill=None,
            outline=EMPTY_STAR_COLOR,
            outer_r=STAR_OUTER,
            inner_r=STAR_INNER,
            outline_width=1,
        )


def _draw_prize_cell(
    draw: ImageDraw.ImageDraw,
    img: Image.Image,
    cx: float,
    cy: float,
    section: int,
    milestone_reached: bool,
) -> None:
    """Draw a prize cell with gold star, icon, and subtle highlight."""
    # Milestone cell highlight
    highlight_color = (
        MILESTONE_HIGHLIGHT_ACTIVE
        if milestone_reached
        else MILESTONE_HIGHLIGHT_INACTIVE
    )
    cell_w, cell_h = CELL_W - 4, CELL_H - 4
    overlay = Image.new("RGBA", (cell_w, cell_h), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(
        [(0, 0), (cell_w - 1, cell_h - 1)],
        radius=6,
        fill=highlight_color,
    )
    paste_x = int(cx - cell_w / 2)
    paste_y = int(cy - cell_h / 2)
    bg_crop = img.crop((paste_x, paste_y, paste_x + cell_w, paste_y + cell_h))
    img.paste(Image.alpha_composite(bg_crop, overlay), (paste_x, paste_y))

    star_cx = cx + 10
    if milestone_reached:
        _paste_gold_star_with_glow(img, star_cx, cy, GOLD_STAR_OUTER, GOLD_STAR_INNER)
    else:
        _paste_star(
            img,
            star_cx,
            cy,
            fill=None,
            outline=EMPTY_STAR_COLOR,
            outer_r=GOLD_STAR_OUTER,
            inner_r=GOLD_STAR_INNER,
            outline_width=1,
        )

    # Prize icon to the left of the star
    _paste_prize_icon(img, cx - 28, cy + 3, section, milestone_reached)


def _darken(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    """Darken a color by a factor (0-1)."""
    return (
        int(color[0] * factor),
        int(color[1] * factor),
        int(color[2] * factor),
    )
