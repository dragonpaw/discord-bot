import pydantic


class IntrosGuildState(pydantic.BaseModel):
    guild_id: int = pydantic.Field(gt=0)
    guild_name: str = ""
    channel_id: int | None = None
    channel_name: str = ""
    required_role_id: int | None = None
    required_role_name: str = ""
