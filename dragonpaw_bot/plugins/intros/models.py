
from dragonpaw_bot.state_store import GuildStateBase


class IntrosGuildState(GuildStateBase):
    channel_id: int | None = None
    channel_name: str = ""
    required_role_id: int | None = None
    required_role_name: str = ""
    missing_role_id: int | None = None
    missing_role_name: str = ""
