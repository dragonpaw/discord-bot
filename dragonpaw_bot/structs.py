import datetime

import hikari
import pydantic

from dragonpaw_bot.plugins.role_menus.models import RolesConfig


# ---------------------------------------------------------------------------- #
#             Configs: The format that we get from the config file.            #
# ---------------------------------------------------------------------------- #
class LobbyConfig(pydantic.BaseModel):
    channel: str
    click_for_rules: bool = False
    kick_after_days: int | None = None
    role: str | None = None
    rules: str | None = None
    welcome_message: str | None = None


class GuildConfig(pydantic.BaseModel):
    lobby: LobbyConfig | None = None
    roles: RolesConfig | None = None


# ---------------------------------------------------------------------------- #
#              States: The thing we keep after setting up the Guild            #
# ---------------------------------------------------------------------------- #
class GuildState(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)

    id: hikari.Snowflake
    name: str

    config_url: str
    config_last: datetime.datetime

    lobby_role_id: hikari.Snowflake | None = None
    lobby_welcome_message: str | None = None
    lobby_channel_id: hikari.Snowflake | None = None
    lobby_click_for_rules: bool = False
    lobby_kick_days: int = 0
    lobby_rules: str = ""
    lobby_rules_message_id: hikari.Snowflake | None = None

    log_channel_id: hikari.Snowflake | None = None
