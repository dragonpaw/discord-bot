# -*- coding: utf-8 -*-
import pydantic


class BirthdayEntry(pydantic.BaseModel):
    user_id: int
    month: int  # 1-12
    day: int  # 1-31
    wishlist_url: str | None = None


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
    guild_id: int
    guild_name: str = ""
    config: BirthdayGuildConfig = BirthdayGuildConfig()
    birthdays: dict[int, BirthdayEntry] = {}
