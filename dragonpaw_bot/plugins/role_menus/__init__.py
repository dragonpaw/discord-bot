from __future__ import annotations

from typing import TYPE_CHECKING

from dragonpaw_bot.plugins.role_menus.commands import handle_role_menu_interaction
from dragonpaw_bot.plugins.role_menus.constants import ROLE_MENU_PREFIX

if TYPE_CHECKING:
    from dragonpaw_bot.utils import InteractionHandler

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    ROLE_MENU_PREFIX: handle_role_menu_interaction,
}
