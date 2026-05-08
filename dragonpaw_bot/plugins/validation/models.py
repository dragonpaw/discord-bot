from __future__ import annotations

import enum
from datetime import datetime  # noqa: TC003

import pydantic


class ValidationStage(enum.StrEnum):
    AWAITING_RULES = "awaiting_rules"
    AWAITING_PHOTOS = "awaiting_photos"
    AWAITING_STAFF = "awaiting_staff"


class ValidationMember(pydantic.BaseModel):
    user_id: int
    joined_at: datetime
    reminder_count: int = pydantic.Field(default=0, ge=0)
    stage: ValidationStage = ValidationStage.AWAITING_RULES
    channel_id: int | None = None  # set when validate channel is created
    photo_count: int = pydantic.Field(default=0, ge=0)


class ValidationGuildState(pydantic.BaseModel):
    guild_id: int
    guild_name: str
    # config
    lobby_channel_id: int | None = None
    validate_category_id: int | None = None
    member_role_id: int | None = None
    staff_role_id: int | None = None
    # welcome message channel links
    about_channel_id: int | None = None
    roles_channel_id: int | None = None
    intros_channel_id: int | None = None
    events_channel_id: int | None = None
    chat_channel_id: int | None = None
    # runtime
    members: list[ValidationMember] = []
