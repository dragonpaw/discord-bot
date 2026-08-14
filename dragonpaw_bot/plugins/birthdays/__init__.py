from __future__ import annotations

from typing import TYPE_CHECKING

from dragonpaw_bot.colors import SOLARIZED_MAGENTA
from dragonpaw_bot.plugins.birthdays import commands
from dragonpaw_bot.plugins.birthdays import config as birthday_config
from dragonpaw_bot.plugins.birthdays import state as birthday_state
from dragonpaw_bot.plugins.birthdays.constants import (
    BIRTHDAY_CONFIG_PREFIX,
    BIRTHDAY_PREFIX,
    BIRTHDAY_START_ID,
)
from dragonpaw_bot.structs import ButtonEntry, ButtonSpec

if TYPE_CHECKING:
    from dragonpaw_bot.utils import InteractionHandler, ModalHandler

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    BIRTHDAY_CONFIG_PREFIX: birthday_config.handle_config_interaction,
    BIRTHDAY_PREFIX: commands.handle_tz_interaction,
}

MODAL_HANDLERS: dict[str, ModalHandler] = {
    BIRTHDAY_PREFIX: commands.handle_birthday_modal,
}


def _is_available(guild_id: int) -> bool:
    """Without an announcement channel there's nowhere to celebrate anyone."""
    return birthday_state.load(guild_id).config.announcement_channel is not None


BUTTON_ENTRY = ButtonEntry(
    key="birthdays",
    title="🎂 Birthday Hoard",
    description=(
        "Tell me when your special day is and I'll add it to my hoard — "
        "dragons are *very* good at keeping track of treasure, and your "
        "birthday counts. I'll make sure the server celebrates you when it "
        "rolls around! 🐾"
    ),
    color=SOLARIZED_MAGENTA,
    buttons=(
        ButtonSpec(custom_id=BIRTHDAY_START_ID, label="Add My Birthday", emoji="🎂"),
    ),
    is_available=_is_available,
)
