# -*- coding: utf-8 -*-
import pydantic


class BirthdayEntry(pydantic.BaseModel):
    user_id: int
    month: int  # 1-12
    day: int  # 1-31
    wishlist_url: str | None = None


class BirthdayGuildConfig(pydantic.BaseModel):
    manage_role: str | None = None  # Role for set-for/remove-for
    list_role: str | None = None  # Role for list command
    announcement_channel: str | None = None  # Channel name
    birthday_role: str | None = None  # Auto-assigned on birthday


class BirthdayGuildState(pydantic.BaseModel):
    guild_id: int
    guild_name: str = ""
    config: BirthdayGuildConfig = BirthdayGuildConfig()
    birthdays: dict[int, BirthdayEntry] = {}
