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
    owner_id: int | None = None
    pending_owner_id: int | None = None


class SubDayGuildConfig(pydantic.BaseModel):
    enroll_role: list[str] = []

    @pydantic.field_validator("enroll_role", mode="before")
    @classmethod
    def _coerce_enroll_role(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return v  # type: ignore[return-value]

    complete_role: str | None = None
    backfill_role: str | None = None
    achievements_channel: str | None = None
    role_13: str | None = "SubChallenge: 13wks"
    role_26: str | None = "SubChallenge: 26wks"
    role_39: str | None = "SubChallenge: 39wks"
    role_52: str | None = "SubChallenge: 52wks"
    prize_13: str = "a $25 gift card"
    prize_26: str = "a tail plug or $60 equivalent"
    prize_39: str = "a Lovense toy or $120 equivalent"
    prize_52: str = "a fantasy dildo or flogger (up to $180)"

    def milestone_roles(self) -> dict[int, str | None]:
        return {13: self.role_13, 26: self.role_26, 39: self.role_39, 52: self.role_52}

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
