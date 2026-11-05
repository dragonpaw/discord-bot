import pydantic

from dragonpaw_bot.state_store import GuildStateBase


class IntrosGuildState(GuildStateBase):
    channel_id: int | None = None
    channel_name: str = ""
    required_role_id: int | None = None
    required_role_name: str = ""
    missing_role_id: int | None = None
    missing_role_name: str = ""
    # Server alias of each intro author, refreshed daily. Discord won't tell us
    # a member's nickname once they've left, and that's when the log needs it.
    aliases: dict[int, str] = pydantic.Field(default_factory=dict)
