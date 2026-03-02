from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import hikari

from dragonpaw_bot.colors import SOLARIZED_VIOLET

WEEKS_DIR = Path(__file__).parent / "weeks"

_cache: dict[int, WeekPrompt] = {}


@dataclass(frozen=True)
class WeekPrompt:
    week: int
    guidepost_title: str
    guidepost_body: str
    thought_1: str
    thought_2: str
    assignment_title: str
    assignment_body: str


def split_sections(text: str) -> dict[str, str]:
    """Split markdown into {heading: body} by ## headers."""
    sections: dict[str, str] = {}
    current_heading = ""
    current_body: list[str] = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current_heading:
                sections[current_heading] = "\n".join(current_body).strip()
            current_heading = line[3:].strip()
            current_body = []
        else:
            current_body.append(line)

    if current_heading:
        sections[current_heading] = "\n".join(current_body).strip()

    return sections


def _parse_file(text: str, week: int) -> WeekPrompt:
    """Parse a week markdown file into a WeekPrompt."""
    sections = split_sections(text)

    # Each file has exactly 4 sections with consistent headers
    guidepost_heading = next(h for h in sections if h.startswith("Guidepost:"))
    assignment_heading = next(
        h for h in sections if h.startswith("Writing Assignment:")
    )

    return WeekPrompt(
        week=week,
        guidepost_title=guidepost_heading.split(":", 1)[1].strip(),
        guidepost_body=sections[guidepost_heading],
        thought_1=sections["Thought 1"],
        thought_2=sections["Thought 2"],
        assignment_title=assignment_heading.split(":", 1)[1].strip(),
        assignment_body=sections[assignment_heading],
    )


def load_week(n: int) -> WeekPrompt:
    """Load and cache a week's prompt. Raises FileNotFoundError if missing."""
    if n in _cache:
        return _cache[n]

    path = WEEKS_DIR / f"week_{n:02d}.md"
    prompt = _parse_file(path.read_text(), week=n)
    _cache[n] = prompt
    return prompt


def load_rules() -> str:
    """Load the shared rules/instructions text."""
    return (WEEKS_DIR / "rules.md").read_text().strip()


def build_prompt_embed(prompt: WeekPrompt) -> hikari.Embed:
    """Build a Discord embed for a week's prompt."""
    embed = hikari.Embed(
        title=f"Where I am Led \u2014 Week {prompt.week}",
        color=SOLARIZED_VIOLET,
    )
    embed.add_field(
        name=f"Guidepost: {prompt.guidepost_title}",
        value=prompt.guidepost_body + "\n\u200b",
        inline=False,
    )
    embed.add_field(
        name="Thought 1",
        value=prompt.thought_1 + "\n\u200b",
        inline=False,
    )
    embed.add_field(
        name="Thought 2",
        value=prompt.thought_2 + "\n\u200b",
        inline=False,
    )
    embed.add_field(
        name=f"Writing Assignment: {prompt.assignment_title}",
        value=prompt.assignment_body,
        inline=False,
    )
    return embed
