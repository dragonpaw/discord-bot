from __future__ import annotations

from typing import TYPE_CHECKING

from dragonpaw_bot.plugins.journal.commands import (
    ADD_MODAL_PREFIX,
    FOLLOWUP_MODAL_PREFIX,
    handle_add_modal,
    handle_followup_modal,
)

if TYPE_CHECKING:
    from dragonpaw_bot.utils import ModalHandler

MODAL_HANDLERS: dict[str, ModalHandler] = {
    ADD_MODAL_PREFIX: handle_add_modal,
    FOLLOWUP_MODAL_PREFIX: handle_followup_modal,
}
