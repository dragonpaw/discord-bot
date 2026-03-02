from __future__ import annotations

from dataclasses import dataclass

import hikari

from dragonpaw_bot.colors import SOLARIZED_CYAN, SOLARIZED_VIOLET, SOLARIZED_YELLOW
from dragonpaw_bot.plugins.subday.constants import (
    MILESTONE_WEEKS,
    TOTAL_WEEKS,
    WEEKS_DIR,
)

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
        title=f"📖 Where I am Led — Week {prompt.week}",
        color=SOLARIZED_VIOLET,
    )
    embed.add_field(
        name=f"🧭 Guidepost: {prompt.guidepost_title}",
        value=prompt.guidepost_body + "\n\u200b",
        inline=False,
    )
    embed.add_field(
        name="💭 Thought 1",
        value=prompt.thought_1 + "\n\u200b",
        inline=False,
    )
    embed.add_field(
        name="💭 Thought 2",
        value=prompt.thought_2 + "\n\u200b",
        inline=False,
    )
    embed.add_field(
        name=f"✍️ Writing Assignment: {prompt.assignment_title}",
        value=prompt.assignment_body,
        inline=False,
    )
    embed.set_footer(text=f"Week {prompt.week} of {TOTAL_WEEKS}")
    return embed


def _progress_bar(week: int) -> str:
    """Build a text progress bar for the current week."""
    filled = week - 1  # weeks *completed* before this one
    total = TOTAL_WEEKS
    bar_len = 13  # one slot per milestone quarter-ish
    done = round(filled / total * bar_len)
    bar = "▓" * done + "░" * (bar_len - done)
    pct = round(filled / total * 100)
    return f"`{bar}` {pct}%"


_MILESTONE_NEAR_THRESHOLD = 3


def build_weekly_dm_embeds(prompt: WeekPrompt) -> list[hikari.Embed]:
    """Build the embeds sent as a weekly advancement DM.

    Returns a greeting embed followed by the prompt embed.
    """
    week = prompt.week

    # Pick a color and flavour based on milestones
    next_milestone = next((m for m in MILESTONE_WEEKS if m >= week), TOTAL_WEEKS)
    weeks_to_milestone = next_milestone - week

    if weeks_to_milestone == 0:
        milestone_note = f"🌟 **This is a milestone week!** Complete it to reach the **Week {next_milestone}** milestone!"
    elif weeks_to_milestone <= _MILESTONE_NEAR_THRESHOLD:
        milestone_note = f"✨ Only **{weeks_to_milestone}** week{'s' if weeks_to_milestone != 1 else ''} until your **Week {next_milestone}** milestone!"
    else:
        milestone_note = f"Next milestone: **Week {next_milestone}** ({weeks_to_milestone} weeks away)"

    greeting = hikari.Embed(
        title="📬 Your New Prompt is Here!",
        description=(
            f"Happy Sunday! Here's your **Week {week}** prompt.\n\n"
            f"{_progress_bar(week)}\n\n"
            f"{milestone_note}\n\n"
            "Take your time, reflect, and write when you're ready. "
            "Show your work to your Owner or check in with staff "
            "when you're done. 💜"
        ),
        color=SOLARIZED_CYAN
        if weeks_to_milestone > _MILESTONE_NEAR_THRESHOLD
        else SOLARIZED_YELLOW,
    )

    return [greeting, build_prompt_embed(prompt)]


def build_owner_dm_embeds(prompt: WeekPrompt, sub_user_id: int) -> list[hikari.Embed]:
    """Build embeds sent to an owner when their sub receives a new prompt.

    Returns a greeting embed mentioning the sub followed by the prompt embed.
    """
    greeting = hikari.Embed(
        title="📬 Your sub has a new prompt!",
        description=(
            f"<@{sub_user_id}> has been sent their **Week {prompt.week}** prompt.\n\n"
            "Here's a copy so you can follow along. 💜"
        ),
        color=SOLARIZED_CYAN,
    )
    return [greeting, build_prompt_embed(prompt)]
