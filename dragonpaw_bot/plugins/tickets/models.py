from __future__ import annotations

import pydantic

from dragonpaw_bot.state_store import GuildStateBase


class OpenTicket(pydantic.BaseModel):
    user_id: int
    channel_id: int
    topic: str


class TicketGuildState(GuildStateBase):
    category_id: int | None = None
    staff_role_id: int | None = None
    required_role_id: int | None = None
    open_tickets: list[OpenTicket] = pydantic.Field(default_factory=list)
