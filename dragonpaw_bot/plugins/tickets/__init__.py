from __future__ import annotations

from typing import TYPE_CHECKING

from dragonpaw_bot.colors import SOLARIZED_ORANGE
from dragonpaw_bot.plugins.tickets import state as tickets_state
from dragonpaw_bot.plugins.tickets.commands import (
    TICKET_OPEN_ID,
    handle_ticket_add_person,
    handle_ticket_add_person_select,
    handle_ticket_close,
    handle_ticket_close_confirm,
    handle_ticket_open_button,
    handle_topic_modal,
)
from dragonpaw_bot.structs import ButtonEntry, ButtonSpec

if TYPE_CHECKING:
    from dragonpaw_bot.utils import InteractionHandler, ModalHandler

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    "ticket_close_confirm:": handle_ticket_close_confirm,  # prefix match — carries channel_id
    "ticket_close": handle_ticket_close,
    "ticket_add_person_select": handle_ticket_add_person_select,
    "ticket_add_person": handle_ticket_add_person,
    TICKET_OPEN_ID: handle_ticket_open_button,
}

MODAL_HANDLERS: dict[str, ModalHandler] = {
    "ticket_topic_modal": handle_topic_modal,
}


def _is_available(guild_id: int) -> bool:
    """Tickets need a staff role — without one there's nobody to ping."""
    return tickets_state.load(guild_id).staff_role_id is not None


BUTTON_ENTRY = ButtonEntry(
    key="tickets",
    title="🆘 Need a Hand?",
    description=(
        "Something on your mind? Poke the button and I'll dig out a private "
        "little den where you and the staff team can talk it through — just "
        "the folks who need to be there, nobody else. No question is too "
        "small, I promise. 🐉"
    ),
    color=SOLARIZED_ORANGE,
    buttons=(
        ButtonSpec(custom_id=TICKET_OPEN_ID, label="Ask an Adultier Adult", emoji="🆘"),
    ),
    is_available=_is_available,
)
