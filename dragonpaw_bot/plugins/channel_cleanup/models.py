import pydantic


class CleanupChannelEntry(pydantic.BaseModel):
    channel_id: int = pydantic.Field(gt=0)
    channel_name: str = pydantic.Field(min_length=1)
    expiry_minutes: int = pydantic.Field(gt=0)


class CleanupGuildState(pydantic.BaseModel):
    guild_id: int = pydantic.Field(gt=0)
    guild_name: str = ""
    channels: list[CleanupChannelEntry] = []
