# -*- coding: utf-8 -*-
from __future__ import annotations

import pydantic


class RoleMenuOptionConfig(pydantic.BaseModel):
    role: str = pydantic.Field(min_length=1)
    emoji: str | None = None
    description: str = pydantic.Field(min_length=1)


class RoleMenuConfig(pydantic.BaseModel):
    name: str = pydantic.Field(min_length=1)
    single: bool = False
    description: str | None = None
    options: list[RoleMenuOptionConfig] = pydantic.Field(min_length=1, max_length=25)


class RolesConfig(pydantic.BaseModel):
    channel: str = pydantic.Field(min_length=1)
    menu: list[RoleMenuConfig] = pydantic.Field(min_length=1)


class RoleMenuState(pydantic.BaseModel):
    menu_index: int = pydantic.Field(ge=0)
    menu_name: str
    message_id: int = pydantic.Field(gt=0)
    single: bool
    option_role_ids: dict[str, int]  # role_name -> role_id


class RoleMenuGuildState(pydantic.BaseModel):
    guild_id: int = pydantic.Field(gt=0)
    guild_name: str = ""
    role_channel_id: int | None = None
    role_names: dict[int, str] = pydantic.Field(default_factory=dict)
    menus: list[RoleMenuState] = pydantic.Field(default_factory=list)
