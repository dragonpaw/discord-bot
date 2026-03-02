# -*- coding: utf-8 -*-
import datetime

import pydantic


class SubDayParticipant(pydantic.BaseModel):
    user_id: int
    current_week: int = 1
    week_completed: bool = False
    signup_date: datetime.datetime
    last_completed_date: datetime.datetime | None = None
    week_sent: bool = False


class SubDayGuildConfig(pydantic.BaseModel):
    enroll_role: str | None = None
    complete_role: str | None = None
    backfill_role: str | None = None
    achievements_channel: str | None = None
    staff_channel: str | None = None
    prize_13: str = "a $25 gift card"
    prize_26: str = "a tail plug or $60 equivalent"
    prize_39: str = "a Lovense toy or $120 equivalent"
    prize_52: str = "a fantasy dildo or flogger (up to $180)"

    def milestone_prizes(self) -> dict[int, str]:
        return {
            13: self.prize_13,
            26: self.prize_26,
            39: self.prize_39,
            52: self.prize_52,
        }


class SubDayGuildState(pydantic.BaseModel):
    guild_id: int
    guild_name: str = ""
    config: SubDayGuildConfig = SubDayGuildConfig()
    participants: dict[int, SubDayParticipant] = {}
