from __future__ import annotations

from typing import TYPE_CHECKING

from dragonpaw_bot.plugins.birthdays import commands
from dragonpaw_bot.plugins.birthdays import config as birthday_config
from dragonpaw_bot.plugins.birthdays.constants import (
    BIRTHDAY_CONFIG_PREFIX,
    BIRTHDAY_PREFIX,
)

if TYPE_CHECKING:
    from dragonpaw_bot.utils import InteractionHandler, ModalHandler

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    BIRTHDAY_CONFIG_PREFIX: birthday_config.handle_config_interaction,
    BIRTHDAY_PREFIX: commands.handle_tz_interaction,
}

MODAL_HANDLERS: dict[str, ModalHandler] = {
    BIRTHDAY_PREFIX: commands.handle_birthday_modal,
}
