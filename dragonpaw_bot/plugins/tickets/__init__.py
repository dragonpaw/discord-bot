from __future__ import annotations

from typing import TYPE_CHECKING

import lightbulb

from dragonpaw_bot.plugins.tickets.commands import (
    HelpCommand,
    handle_ticket_add_person,
    handle_ticket_add_person_select,
    handle_ticket_close,
    handle_ticket_close_confirm,
    handle_topic_modal,
)

if TYPE_CHECKING:
    from dragonpaw_bot.utils import InteractionHandler, ModalHandler

loader = lightbulb.Loader()
loader.command(HelpCommand)

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    "ticket_close_confirm:": handle_ticket_close_confirm,  # prefix match — carries channel_id
    "ticket_close": handle_ticket_close,
    "ticket_add_person_select": handle_ticket_add_person_select,
    "ticket_add_person": handle_ticket_add_person,
}

MODAL_HANDLERS: dict[str, ModalHandler] = {
    "ticket_topic_modal": handle_topic_modal,
}
