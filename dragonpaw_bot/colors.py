import hikari
import palettable

SOLARIZED_BASE03 = hikari.Color.from_hex_code("#002b36")
SOLARIZED_BASE02 = hikari.Color.from_hex_code("#073642")
SOLARIZED_BASE01 = hikari.Color.from_hex_code("#586e75")
SOLARIZED_BASE00 = hikari.Color.from_hex_code("#657b83")
SOLARIZED_BASE0 = hikari.Color.from_hex_code("#839496")
SOLARIZED_BASE1 = hikari.Color.from_hex_code("#93a1a1")
SOLARIZED_BASE2 = hikari.Color.from_hex_code("#eee8d5")
SOLARIZED_BASE3 = hikari.Color.from_hex_code("#fdf6e3")
SOLARIZED_YELLOW = hikari.Color.from_hex_code("#b58900")
SOLARIZED_ORANGE = hikari.Color.from_hex_code("#cb4b16")
SOLARIZED_RED = hikari.Color.from_hex_code("#dc322f")
SOLARIZED_MAGENTA = hikari.Color.from_hex_code("#d33682")
SOLARIZED_VIOLET = hikari.Color.from_hex_code("#6c71c4")
SOLARIZED_BLUE = hikari.Color.from_hex_code("#268bd2")
SOLARIZED_CYAN = hikari.Color.from_hex_code("#2aa198")
SOLARIZED_GREEN = hikari.Color.from_hex_code("#859900")

COLORS = "Cube1_{}"  # https://jiffyclub.github.io/palettable/


def rainbow(n: int) -> list[tuple[int, int, int]]:
    # This is how to do it using only colorsys, but the colors are not as nice.
    # end = 2 / 3
    # as_float = [colorsys.hls_to_rgb(end * i / (n - 1), 0.5, 1) for i in range(n)]
    # return [(int(x[0] * 255), int(x[1] * 255), int(x[2] * 255)) for x in as_float]
    # palettable maps start at 2 colors — slice down for a single-item request
    return palettable.mycarta.get_map(COLORS.format(max(n, 2))).colors[:n]
