# -*- coding: utf-8 -*-
from __future__ import annotations

import lightbulb
import structlog

from dragonpaw_bot.plugins.role_menus.commands import (
    configure_guild,
    configure_role_menus,
    handle_role_menu_interaction,
    parse_role_config,
)
from dragonpaw_bot.plugins.role_menus.constants import ROLE_MENU_PREFIX
from dragonpaw_bot.utils import InteractionHandler

__all__ = [
    "INTERACTION_HANDLERS",
    "configure_guild",
    "configure_role_menus",
    "parse_role_config",
]

logger = structlog.get_logger(__name__)

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    ROLE_MENU_PREFIX: handle_role_menu_interaction,
}

loader = lightbulb.Loader()
