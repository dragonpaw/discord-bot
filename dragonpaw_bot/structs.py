import dataclasses
import datetime
from collections.abc import Callable

import hikari
import pydantic


# ---------------------------------------------------------------------------- #
#              States: The thing we keep after setting up the Guild            #
# ---------------------------------------------------------------------------- #
class GuildState(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)

    id: hikari.Snowflake
    name: str

    config_url: str
    config_last: datetime.datetime

    log_channel_id: hikari.Snowflake | None = None
    general_channel_id: hikari.Snowflake | None = None
    button_channel_id: hikari.Snowflake | None = None


# ---------------------------------------------------------------------------- #
#            Button channel: what a plugin offers to the button channel        #
# ---------------------------------------------------------------------------- #
# Dataclasses rather than pydantic models: these are code-defined constants that
# are never parsed from user input nor persisted, and a Callable field would
# force arbitrary_types_allowed for nothing.


@dataclasses.dataclass(frozen=True)
class ButtonSpec:
    """One button on a button channel card.

    ``custom_id`` must be routable by bot.py's interaction dispatcher — i.e. the
    plugin that declares it also registers a handler for it.
    """

    custom_id: str
    label: str
    emoji: str


@dataclasses.dataclass(frozen=True)
class ButtonEntry:
    """A plugin's card in the button channel: an embed plus one or two buttons."""

    key: str
    title: str
    description: str
    color: hikari.Color
    buttons: tuple[ButtonSpec, ...]
    is_available: Callable[[int], bool]
