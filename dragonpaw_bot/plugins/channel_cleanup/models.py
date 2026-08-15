import pydantic

from dragonpaw_bot.state_store import GuildStateBase


class CleanupChannelEntry(pydantic.BaseModel):
    channel_id: int = pydantic.Field(gt=0)
    channel_name: str = pydantic.Field(min_length=1)
    expiry_minutes: int = pydantic.Field(gt=0)


class CleanupGuildState(GuildStateBase):
    channels: list[CleanupChannelEntry] = pydantic.Field(default_factory=list)
