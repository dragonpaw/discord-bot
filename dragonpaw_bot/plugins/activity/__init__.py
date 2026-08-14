"""Activity tracker plugin: records member engagement across messages, reactions, and VC."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dragonpaw_bot.colors import SOLARIZED_CYAN
from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.commands import handle_score_interaction
from dragonpaw_bot.structs import ButtonEntry, ButtonSpec

if TYPE_CHECKING:
    from dragonpaw_bot.utils import InteractionHandler

ACTIVITY_SCORE_ID = "activity_score"

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    ACTIVITY_SCORE_ID: handle_score_interaction,
}


BUTTON_ENTRY = ButtonEntry(
    key="activity",
    title="📊 How's Your Hoard Looking?",
    description=(
        "Curious how chatty you've been lately? Give this a poke and I'll "
        "rummage through my hoard and show you your very own activity score, "
        "pretty chart and all. Only you can see it — I'm not one to gossip. 🐉"
    ),
    color=SOLARIZED_CYAN,
    buttons=(
        ButtonSpec(custom_id=ACTIVITY_SCORE_ID, label="My Activity Score", emoji="📊"),
    ),
    is_available=activity_state.is_configured,
)
