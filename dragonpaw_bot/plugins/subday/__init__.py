from __future__ import annotations

from typing import TYPE_CHECKING

from dragonpaw_bot.colors import SOLARIZED_VIOLET
from dragonpaw_bot.plugins.subday import commands
from dragonpaw_bot.plugins.subday import config as subday_config
from dragonpaw_bot.plugins.subday import state as subday_state
from dragonpaw_bot.plugins.subday.constants import (
    SUBDAY_ABOUT_ID,
    SUBDAY_CFG_ROLE_PREFIX,
    SUBDAY_CONFIG_PREFIX,
    SUBDAY_OWNER_REQUEST_PREFIX,
    SUBDAY_SIGNUP_ID,
)
from dragonpaw_bot.structs import ButtonEntry, ButtonSpec

if TYPE_CHECKING:
    from dragonpaw_bot.utils import InteractionHandler

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    SUBDAY_OWNER_REQUEST_PREFIX: commands.handle_owner_interaction,
    SUBDAY_SIGNUP_ID: commands.handle_signup_interaction,
    SUBDAY_ABOUT_ID: commands.handle_about_interaction,
    SUBDAY_CONFIG_PREFIX: subday_config.handle_config_interaction,
    SUBDAY_CFG_ROLE_PREFIX: subday_config.handle_config_interaction,
}


BUTTON_ENTRY = ButtonEntry(
    key="subday",
    title="📖 Where I am Led",
    description=(
        "A 52-week guided journal for service submissives — one gentle set of "
        "prompts every Sunday, at whatever pace suits you. Nobody reads your "
        "answers but you (and your Owner, if you have one), and there are "
        "treats waiting at each milestone. *curls tail approvingly* 🐉"
    ),
    color=SOLARIZED_VIOLET,
    buttons=(
        ButtonSpec(custom_id=SUBDAY_SIGNUP_ID, label="Join Where I am Led", emoji="📖"),
        ButtonSpec(custom_id=SUBDAY_ABOUT_ID, label="What is this?", emoji="❓"),
    ),
    is_available=subday_state.is_configured,
)
