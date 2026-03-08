# -*- coding: utf-8 -*-
"""Shared duration parsing/formatting utilities."""
from __future__ import annotations

import re

_PART = re.compile(
    r"(\d+)\s*(w(?:eeks?)?|d(?:ays?)?|h(?:ours?)?|m(?:in(?:utes?)?)?)",
    re.IGNORECASE,
)
_UNITS: dict[str, int] = {"w": 10080, "d": 1440, "h": 60, "m": 1}


def parse_duration_minutes(s: str) -> int:
    """Parse a human-readable duration string into minutes.

    Accepts formats like: 30m, 6h, 2d, 1w, 1d12h, 1 week, 30 minutes, etc.
    Raises ValueError on unrecognisable input or zero/negative result.
    """
    parts = _PART.findall(s.strip())
    if not parts:
        raise ValueError(
            f"Couldn't parse {s!r}. Try formats like: 30m, 6h, 1d, 7d, 1w, 1d12h"
        )
    total = sum(int(n) * _UNITS[unit[0].lower()] for n, unit in parts)
    if total <= 0:
        raise ValueError("Duration must be positive")
    return total


def format_duration(minutes: int) -> str:
    """Format a minute count into a compact human-readable string.

    Examples: 1440 → "1d", 90 → "1h 30m", 10080 → "1w"
    """
    weeks, minutes = divmod(minutes, 10080)
    days, minutes = divmod(minutes, 1440)
    hours, mins = divmod(minutes, 60)
    parts = []
    if weeks:
        parts.append(f"{weeks}w")
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    return " ".join(parts) if parts else "0m"
