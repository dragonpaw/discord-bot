from __future__ import annotations

import pydantic


class OpenTicket(pydantic.BaseModel):
    user_id: int
    channel_id: int
    topic: str


class TicketGuildState(pydantic.BaseModel):
    guild_id: int
    guild_name: str = ""
    category_id: int | None = None
    staff_role_id: int | None = None
    required_role_id: int | None = None
    open_tickets: list[OpenTicket] = []
