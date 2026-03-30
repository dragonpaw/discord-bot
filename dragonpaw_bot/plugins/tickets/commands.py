from __future__ import annotations

import re


def _sanitize_channel_name(display_name: str) -> str:
    """Convert a display name to a valid Discord channel name: help-{name}."""
    name = display_name.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name)
    name = name.strip("-")
    return f"help-{name}"[:100]
