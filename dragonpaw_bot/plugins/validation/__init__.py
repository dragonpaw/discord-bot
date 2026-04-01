from __future__ import annotations

from typing import TYPE_CHECKING

from dragonpaw_bot.plugins.validation.commands import (
    handle_approve_button,
    handle_approve_modal,
    handle_rules_agreed,
)
from dragonpaw_bot.plugins.validation.commands import (
    loader as loader,
)

if TYPE_CHECKING:
    from dragonpaw_bot.utils import InteractionHandler, ModalHandler

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    "validation_approve:": handle_approve_button,  # prefix match — carries channel_id
    "validation_rules_agreed:": handle_rules_agreed,  # prefix match — carries user_id
}

MODAL_HANDLERS: dict[str, ModalHandler] = {
    "validation_approve_modal:": handle_approve_modal,  # prefix match — carries channel_id
}
