import datetime

import hikari
import pydantic
from pydantic import ConfigDict


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


class RoleMenuOptionConfig(pydantic.BaseModel):
    role: str
    emoji: str
    description: str


class RoleMenuConfig(pydantic.BaseModel):
    name: str
    single: bool = False
    description: str | None = None
    options: list[RoleMenuOptionConfig]


class RolesConfig(pydantic.BaseModel):
    channel: str
    menu: list[RoleMenuConfig]


class GuildConfig(pydantic.BaseModel):
    lobby: LobbyConfig | None = None
    roles: RolesConfig | None = None
    log_channel: str | None = None


# ---------------------------------------------------------------------------- #
#              States: The thing we keep after seting up the Guild             #
# ---------------------------------------------------------------------------- #
class RoleMenuOptionState(pydantic.BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    add_role_id: hikari.Snowflake
    remove_role_ids: list[hikari.Snowflake]


class GuildState(pydantic.BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: hikari.Snowflake
    name: str

    config_url: str
    # config_size: int
    config_last: datetime.datetime

    lobby_role_id: hikari.Snowflake | None = None
    lobby_welcome_message: str | None = None
    lobby_channel_id: hikari.Snowflake | None = None
    lobby_click_for_rules: bool = False
    lobby_kick_days: int = 0
    lobby_rules: str = ""
    lobby_rules_message_id: hikari.Snowflake | None = None

    # Role management
    role_channel_id: hikari.Snowflake | None = None
    # Key is (meddage.id,emoji)
    role_emojis: dict[tuple[hikari.Snowflake, str], RoleMenuOptionState]
    role_names: dict[hikari.Snowflake, str]

    log_channel_id: hikari.Snowflake | None = None
