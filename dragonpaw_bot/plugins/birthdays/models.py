# -*- coding: utf-8 -*-
import calendar
import datetime
import zoneinfo

import pydantic

_FEB = 2
_LEAP_DAY = 29


class BirthdayEntry(pydantic.BaseModel):
    user_id: int = pydantic.Field(gt=0)
    month: int = pydantic.Field(ge=1, le=12)
    day: int = pydantic.Field(ge=1, le=31)
    wishlist_url: str | None = None
    timezone: str | None = None  # IANA timezone (e.g. "America/New_York"), None = UTC
    last_announced: datetime.date | None = None

    @pydantic.model_validator(mode="after")
    def _validate_month_day(self) -> "BirthdayEntry":
        max_day = (
            _LEAP_DAY
            if self.month == _FEB
            else calendar.monthrange(2000, self.month)[1]
        )
        if self.day > max_day:
            msg = f"Day {self.day} is not valid for month {self.month}"
            raise ValueError(msg)
        return self

    @pydantic.field_validator("timezone", mode="after")
    @classmethod
    def _validate_timezone(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                zoneinfo.ZoneInfo(v)
            except (KeyError, zoneinfo.ZoneInfoNotFoundError):
                msg = f"Invalid IANA timezone: {v}"
                raise ValueError(msg)  # noqa: B904
        return v


class BirthdayGuildConfig(pydantic.BaseModel):
    register_role: list[str] = []  # Role(s) required to self-register

    @pydantic.field_validator("register_role", mode="before")
    @classmethod
    def _coerce_register_role(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return v  # type: ignore[return-value]

    manage_role: str | None = None  # Role for set-for/remove-for
    list_role: str | None = None  # Role for list command
    announcement_channel: str | None = None  # Channel name
    birthday_role: str | None = None  # Auto-assigned on birthday


class BirthdayGuildState(pydantic.BaseModel):
    guild_id: int = pydantic.Field(gt=0)
    guild_name: str = ""
    config: BirthdayGuildConfig = BirthdayGuildConfig()
    birthdays: dict[int, BirthdayEntry] = {}

    @pydantic.model_validator(mode="after")
    def _check_birthday_keys(self) -> "BirthdayGuildState":
        for key, entry in self.birthdays.items():
            if key != entry.user_id:
                msg = f"Birthday dict key {key} does not match entry user_id {entry.user_id}"
                raise ValueError(msg)
        return self
