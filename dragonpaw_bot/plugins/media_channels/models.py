# -*- coding: utf-8 -*-
import pydantic


class MediaChannelEntry(pydantic.BaseModel):
    channel_id: int = pydantic.Field(gt=0)
    channel_name: str = pydantic.Field(min_length=1)
    redirect_channel_id: int | None = None  # Per-channel redirect hint
    redirect_channel_name: str | None = None
    expiry_minutes: int | None = pydantic.Field(default=None, gt=0)


class MediaGuildState(pydantic.BaseModel):
    guild_id: int = pydantic.Field(gt=0)
    guild_name: str = ""
    channels: list[MediaChannelEntry] = []
