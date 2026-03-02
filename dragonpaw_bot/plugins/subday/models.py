import datetime

import pydantic


class SubDayParticipant(pydantic.BaseModel):
    user_id: int
    current_week: int = 1
    week_completed: bool = False
    signup_date: datetime.datetime
    last_completed_date: datetime.datetime | None = None
    week_sent: bool = False


class SubDayGuildState(pydantic.BaseModel):
    guild_id: int
    guild_name: str = ""
    participants: dict[int, SubDayParticipant] = {}
