from __future__ import annotations

import lightbulb
import structlog

logger = structlog.get_logger(__name__)

loader = lightbulb.Loader()
journal_group = lightbulb.Group("journal", "Member journal")


def staff_blocked(ctx: lightbulb.Context, staff_role_id: int | None) -> str | None:
    """Return a refusal message if the caller may not use the journal."""
    if staff_role_id is None:
        return (
            "*tilts head* I don't have a staff role set up yet, so I can't tell "
            "who's allowed to peek at my journal! Ask an admin to run "
            "`/config journal set` first. 🐉"
        )
    if not ctx.member or staff_role_id not in {int(r) for r in ctx.member.role_ids}:
        return "*curls protectively around my journal* Sorry, this one's staff-only! 🐉"
    return None


loader.command(journal_group)
