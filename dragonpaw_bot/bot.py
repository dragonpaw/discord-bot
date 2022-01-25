#!/usr/bin/env python
import datetime
import io
import logging
import pickle
from os import environ
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Mapping, Optional, Sequence, Union

import hikari
import hikari.messages
import lightbulb
import safer
import toml
import uvloop
from dotenv import load_dotenv
from emojis.db.db import EMOJI_DB

from dragonpaw_bot import http, structs, utils
from dragonpaw_bot.colors import SOLARIZED_RED
from dragonpaw_bot.plugins.lobby import configure_lobby
from dragonpaw_bot.plugins.role_menus import configure_role_menus

load_dotenv()

logging.getLogger("dragonpaw_bot").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

uvloop.install()

ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT_DIR / "state"

VALIDATION_ERROR = (
    "The config for this server failed to pass validation. Below are the errors. "
    "(Please be aware, programmers start counting at 0, so `menus.1.description` "
    "means the description of your **2nd** menu!"
)
OAUTH_PERMISSIONS = (
    hikari.Permissions.SEND_MESSAGES
    | hikari.Permissions.MANAGE_ROLES
    | hikari.Permissions.MANAGE_MESSAGES
    | hikari.Permissions.READ_MESSAGE_HISTORY
    | hikari.Permissions.ADD_REACTIONS
    | hikari.Permissions.KICK_MEMBERS
    | hikari.Permissions.USE_APPLICATION_COMMANDS
).value
CLIENT_ID = 791831545475235860
CLIENT_ID_TEST = 930665318139953192
OAUTH_URL = "https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&permissions={OAUTH_PERMISSIONS}&scope=applications.commands%20bot"
INTENTS = (
    hikari.Intents.GUILD_MESSAGES
    | hikari.Intents.GUILD_MESSAGE_REACTIONS
    | hikari.Intents.GUILDS
    | hikari.Intents.GUILD_MEMBERS
    | hikari.Intents.GUILD_EMOJIS
).value

DRAGONPAW_GUILD = 216602233083527168


class DragonpawBot(lightbulb.BotApp):
    def __init__(self):
        super().__init__(
            token=environ["BOT_TOKEN"],
            default_enabled_guilds=DRAGONPAW_GUILD,
            intents=INTENTS,
        )
        self._state: Dict[hikari.Snowflake, structs.GuildState] = {}
        self.user: Optional[hikari.OwnUser]

    def state(self, guild_id: hikari.Snowflake) -> Optional[structs.GuildState]:
        if guild_id not in self._state:
            state = self.state_load_pickle(guild_id=guild_id)
            if state:
                self._state[guild_id] = state
        return self._state.get(guild_id)

    def state_update(self, state: structs.GuildState):
        self._state[state.id] = state
        self.state_save_pickle(state=state)

    # ---------------------------------------------------------------------------- #
    #                                 File handling                                #
    # ---------------------------------------------------------------------------- #

    def state_path(self, guild_id: hikari.Snowflake, extention="toml"):
        return Path(STATE_DIR, str(guild_id) + "." + extention)

    def state_save_pickle(self, state: structs.GuildState):
        filename = self.state_path(state.id, extention="pickle")
        logger.info("G=%r Saving state to: %s", state.name, filename)
        with safer.open(filename, "wb") as f:
            pickle.dump(obj=state.dict(), file=f)

    def state_load_pickle(
        self, guild_id: hikari.Snowflake
    ) -> Optional[structs.GuildState]:
        filename = self.state_path(guild_id=guild_id, extention="pickle")

        if not filename.exists():
            logger.debug("No state file for guild: %d", guild_id)
            return None

        logger.debug("Loading state from: %s", filename)
        try:
            with safer.open(filename, "rb") as f:
                return structs.GuildState.parse_obj(pickle.load(f))
        except Exception as e:
            logger.exception("Error loading file: %r", e)
            return None


bot = DragonpawBot()

# ---------------------------------------------------------------------------- #
#                                   Handlers                                   #
# ---------------------------------------------------------------------------- #


@bot.listen()
async def on_ready(event: hikari.ShardReadyEvent) -> None:
    """Post-initalization for the bot."""
    logger.info("Connected to Discord as %r", event.my_user)
    logger.info(
        "Use this URL to add to a server: %s",
        OAUTH_URL.format(CLIENT_ID=CLIENT_ID, OAUTH_PERMISSIONS=OAUTH_PERMISSIONS),
    )
    logger.debug(
        "Use this URL to add the TEST version a server: %s",
        OAUTH_URL.format(CLIENT_ID=CLIENT_ID_TEST, OAUTH_PERMISSIONS=OAUTH_PERMISSIONS),
    )
    bot.user = event.my_user


@bot.listen()
async def on_guild_available(event: hikari.GuildAvailableEvent):
    state = bot.state(event.guild_id)
    if state:
        logger.info("G=%r State loaded from disk, resuming services", state.name)
    else:
        guild = event.get_guild()
        name = (guild and guild.name) or event.guild_id
        logger.info("G=%r No state found, so nothing to do.", name)


@bot.listen()
async def on_guild_join(event: hikari.GuildJoinEvent):
    guild = await bot.rest.fetch_guild(guild=event.guild_id)
    logger.info("G=%r Joined server.", guild.name)


# ---------------------------------------------------------------------------- #
#                                   Commands                                   #
# ---------------------------------------------------------------------------- #


@bot.command
@lightbulb.add_checks(lightbulb.has_guild_permissions(hikari.Permissions.ADMINISTRATOR))
@lightbulb.option("url", "Link to the config you wish to use")
@lightbulb.command(
    "config",
    description="Configure Dragonpaw Bot via a url to a TOML file.",
    ephemeral=True,
)
@lightbulb.implements(lightbulb.SlashCommand)
async def setup(ctx: lightbulb.Context) -> None:
    if not ctx.guild_id:
        logger.error("Interaction without a guild?!: %r", ctx)
        return

    await ctx.respond("Config loading now...")

    g = await bot.rest.fetch_guild(guild=ctx.guild_id)
    logger.info("G=%r Setting up guild with file %r", g.name, ctx.options.url)
    assert isinstance(ctx.app, DragonpawBot)
    await configure_guild(bot=ctx.app, guild=g, url=ctx.options.url)


# ---------------------------------------------------------------------------- #
#                                Config handling                               #
# ---------------------------------------------------------------------------- #


def config_parse_toml(guild: hikari.Guild, text: str):
    logger.info("G=%r Loading TOML config for guild: %r", guild.name, guild)

    data = toml.loads(text)
    return structs.GuildConfig.parse_obj(data)


async def configure_guild(bot: DragonpawBot, guild: hikari.Guild, url: str):
    """Load the config for a guild and start setting up everything there."""

    if url.startswith("https://gist.github.com"):
        config_text = await http.get_gist(url)
    else:
        config_text = await http.get_text(url)

    config = config_parse_toml(guild=guild, text=config_text)
    role_map = await utils.guild_roles(bot=bot, guild=guild)

    errors = []
    state = structs.GuildState(
        id=guild.id,
        name=guild.name,
        config_url=url,
        config_last=datetime.datetime.now(),
        role_names={r.id: r.name for r in role_map.values()},
        role_emojis={},
    )

    # Start setting up the guild
    if config.roles:
        errors.extend(
            await configure_role_menus(
                bot=bot,
                guild=guild,
                config=config.roles,
                state=state,
                role_map=role_map,
            )
        )

    if config.lobby:
        errors.extend(
            await configure_lobby(
                bot=bot,
                guild=guild,
                config=config.lobby,
                state=state,
                role_map=role_map,
            )
        )

    for e in errors:
        await utils.report_errors(bot=bot, guild_id=guild.id, error=e)

    # logger.debug("Final state: %r", state)
    bot.state_update(state)


bot.load_extensions("dragonpaw_bot.plugins.lobby")
bot.load_extensions("dragonpaw_bot.plugins.role_menus")