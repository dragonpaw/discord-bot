from __future__ import annotations

import io
import math
import random
from pathlib import Path

import hikari
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------- #
#                                   Constants                                   #
# ---------------------------------------------------------------------------- #

FONTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fonts"
FONT_TITLE = FONTS_DIR / "DaxCondensed-Bold.ttf"
FONT_NUMBERS = FONTS_DIR / "DaxCondensed-Regular.ttf"
FONT_MILESTONE = FONTS_DIR / "DaxCondensed-Medium.ttf"
FONT_USERNAME = FONTS_DIR / "Caveat-Bold.ttf"

TOTAL_WEEKS = 52
COLS = 7
ROWS_PER_SECTION = 2
CELLS_PER_SECTION = COLS * ROWS_PER_SECTION  # 14
WEEKS_PER_SECTION = CELLS_PER_SECTION - 1  # 13 (last cell is the prize cell)
SECTIONS = 4
MILESTONE_WEEKS = {13, 26, 39, 52}

# Supersampling scale for anti-aliased stars
SS = 3

# Canvas
PADDING = 24
TITLE_HEIGHT = 48
SECTION_GAP = 8
DIVIDER_HEIGHT = 2
CELL_W = 100
CELL_H = 44

GRID_WIDTH = COLS * CELL_W
CANVAS_WIDTH = GRID_WIDTH + 2 * PADDING
GRID_HEIGHT = SECTIONS * ROWS_PER_SECTION * CELL_H + (SECTIONS - 1) * (
    SECTION_GAP + DIVIDER_HEIGHT
)
CANVAS_HEIGHT = PADDING + TITLE_HEIGHT + GRID_HEIGHT + PADDING

# Star geometry — golden-ratio-based inner radius for a classic star shape
STAR_OUTER = 18
STAR_INNER = STAR_OUTER * 0.38
GOLD_STAR_OUTER = 22
GOLD_STAR_INNER = GOLD_STAR_OUTER * 0.38

# Colors
BG_COLOR = (255, 255, 255)
DIVIDER_COLOR = (180, 180, 180)
TEXT_COLOR = (60, 60, 60)
NUMBER_COLOR = (80, 80, 80)
EMPTY_STAR_COLOR = (200, 200, 200)
GOLD = (255, 215, 0)
GOLD_DARK = (200, 160, 0)
GOLD_GLOW = (255, 230, 80, 90)
HOT_PINK = (255, 20, 100)

STICKER_COLORS = [
    (220, 40, 40),  # red
    (255, 20, 100),  # hot pink
    (50, 80, 220),  # royal blue
    (70, 170, 240),  # sky blue
    (40, 170, 70),  # green
    (100, 210, 50),  # lime
    (255, 210, 30),  # yellow
    (255, 140, 30),  # orange
    (140, 50, 200),  # purple
    (0, 180, 170),  # teal
    (200, 40, 180),  # magenta
    (255, 110, 90),  # coral
    (180, 30, 60),  # crimson
    (255, 80, 180),  # rose
    (30, 50, 180),  # navy
    (100, 200, 220),  # aqua
    (20, 130, 50),  # forest
    (180, 220, 40),  # chartreuse
    (240, 180, 50),  # amber
    (220, 90, 20),  # rust
    (100, 40, 160),  # indigo
    (60, 210, 140),  # mint
    (160, 20, 120),  # plum
    (230, 70, 50),  # vermilion
]

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
    outline_width: int = 2,
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
    return sprite.resize((size, size), Image.LANCZOS)


def _paste_star(
    img: Image.Image,
    cx: float,
    cy: float,
    fill: tuple[int, int, int] | None,
    outline: tuple[int, int, int],
    outer_r: float,
    inner_r: float,
    rotation: float = 0.0,
    outline_width: int = 2,
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
ICON_SIZE = 28

# Module-level cache for loaded + resized icon sprites
_icon_cache: dict[str, Image.Image] = {}


def _load_icon(filename: str) -> Image.Image:
    """Load a prize icon PNG, resize to ICON_SIZE, and cache it."""
    if filename not in _icon_cache:
        icon = Image.open(ICONS_DIR / filename).convert("RGBA")
        icon.thumbnail((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
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
#                             Main render function                              #
# ---------------------------------------------------------------------------- #


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
    week_jitter_y = [rng.uniform(-2, 2) for _ in range(TOTAL_WEEKS)]

    img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (*BG_COLOR, 255))
    draw = ImageDraw.Draw(img)

    # Load fonts
    title_font = ImageFont.truetype(str(FONT_TITLE), 32)
    username_font = ImageFont.truetype(str(FONT_USERNAME), 38)
    number_font = ImageFont.truetype(str(FONT_NUMBERS), 16)

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

    # ---- Grid ----
    # Determine the last completed week for rendering
    if current_week > TOTAL_WEEKS:
        last_completed = TOTAL_WEEKS
    elif week_completed:
        last_completed = current_week
    else:
        last_completed = current_week - 1

    grid_top = PADDING + TITLE_HEIGHT
    week_num = 1

    for section in range(SECTIONS):
        section_y = grid_top + section * (
            ROWS_PER_SECTION * CELL_H + SECTION_GAP + DIVIDER_HEIGHT
        )

        # Draw section divider (not before first section)
        if section > 0:
            div_y = section_y - SECTION_GAP // 2
            draw.line(
                [(PADDING, div_y), (CANVAS_WIDTH - PADDING, div_y)],
                fill=DIVIDER_COLOR,
                width=DIVIDER_HEIGHT,
            )

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

    # ---- Border around the whole grid ----
    draw.rectangle(
        [
            (PADDING - 1, grid_top - 1),
            (CANVAS_WIDTH - PADDING + 1, grid_top + GRID_HEIGHT + 1),
        ],
        outline=DIVIDER_COLOR,
        width=2,
    )

    # ---- Export as RGB PNG ----
    rgb = img.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
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
        (cx - 28 - nw / 2, cy - (nb[3] - nb[1]) / 2 - 1),
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
            outline_width=2,
        )


def _draw_prize_cell(
    draw: ImageDraw.ImageDraw,
    img: Image.Image,
    cx: float,
    cy: float,
    section: int,
    milestone_reached: bool,
) -> None:
    """Draw a prize cell with gold star and icon."""
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
            outline_width=2,
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
