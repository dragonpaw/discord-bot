import calendar
import datetime
import zoneinfo

import pydantic

from dragonpaw_bot.state_store import GuildStateBase


class BirthdayEntry(pydantic.BaseModel):
    user_id: int = pydantic.Field(gt=0)
    month: int = pydantic.Field(ge=1, le=12)
    day: int = pydantic.Field(ge=1, le=31)
    wishlist_url: str | None = None
    timezone: str | None = None  # IANA timezone (e.g. "America/New_York"), None = UTC
    last_announced: datetime.date | None = None

    @pydantic.model_validator(mode="after")
    def _validate_month_day(self) -> "BirthdayEntry":
        # monthrange in a leap year (2000) already allows Feb 29
        max_day = calendar.monthrange(2000, self.month)[1]
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
                raise ValueError(msg)
        return v


class BirthdayGuildConfig(pydantic.BaseModel):
    register_role: list[str] = []  # Role(s) required to self-register

    manage_role: str | None = None  # Role for remove-for
    announcement_channel: str | None = None  # Channel name
    birthday_role: str | None = None  # Auto-assigned on birthday


class BirthdayGuildState(GuildStateBase):
    config: BirthdayGuildConfig = BirthdayGuildConfig()
    birthdays: dict[int, BirthdayEntry] = pydantic.Field(default_factory=dict)

    @pydantic.model_validator(mode="after")
    def _check_birthday_keys(self) -> "BirthdayGuildState":
        for key, entry in self.birthdays.items():
            if key != entry.user_id:
                msg = f"Birthday dict key {key} does not match entry user_id {entry.user_id}"
                raise ValueError(msg)
        return self
