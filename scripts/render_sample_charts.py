"""Render sample star charts at various progress levels for visual inspection."""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root so we can import the bot package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dragonpaw_bot.plugins.subday.chart import render_star_chart  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "sample_charts"

SAMPLES = [
    ("early_progress", "Luna", 4, True),
    ("mid_progress", "StarGazer", 20, False),
    ("first_milestone", "Moonbeam", 13, True),
    ("near_completion", "Phoenix", 48, True),
    ("graduated", "Celestia", 53, True),
]


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    for name, username, week, completed in SAMPLES:
        attachment = render_star_chart(username, week, completed)
        out_path = OUTPUT_DIR / f"{name}.png"
        out_path.write_bytes(attachment.data)
        print(f"  Wrote {out_path}")
    print(f"\nAll {len(SAMPLES)} sample charts written to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
