import datetime

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
