from __future__ import annotations

from typing import TYPE_CHECKING

from dragonpaw_bot.plugins.subday import commands
from dragonpaw_bot.plugins.subday import config as subday_config
from dragonpaw_bot.plugins.subday.constants import (
    SUBDAY_CFG_ROLE_PREFIX,
    SUBDAY_CONFIG_PREFIX,
    SUBDAY_OWNER_REQUEST_PREFIX,
    SUBDAY_SIGNUP_ID,
)

if TYPE_CHECKING:
    from dragonpaw_bot.utils import InteractionHandler

INTERACTION_HANDLERS: dict[str, InteractionHandler] = {
    SUBDAY_OWNER_REQUEST_PREFIX: commands.handle_owner_interaction,
    SUBDAY_SIGNUP_ID: commands.handle_signup_interaction,
    SUBDAY_CONFIG_PREFIX: subday_config.handle_config_interaction,
    SUBDAY_CFG_ROLE_PREFIX: subday_config.handle_config_interaction,
}
