#!/usr/bin/env python
import datetime
import logging
import pickle
import tomllib
from os import environ
from pathlib import Path
from typing import Any

import dotenv
import hikari
import hikari.messages
import lightbulb
import safer
import uvloop
import yaml

from dragonpaw_bot import http, structs, utils
from dragonpaw_bot.plugins.lobby import configure_lobby
from dragonpaw_bot.plugins.role_menus import configure_role_menus

dotenv.load_dotenv()

logging.getLogger("dragonpaw_bot").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

uvloop.install()

ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT_DIR / "state"

# ACTIVITY = "Doing bot things, thinking bot thoughts..."
VALIDATION_ERROR = (
    "The config for this server failed to pass validation. Below are the errors. "
    "(Please be aware, programmers start counting at 0, so `menus.1.description` "
    "means the description of your **2nd** menu!"
)
OAUTH_PERMISSIONS = (
    hikari.Permissions.SEND_MESSAGES
    | hikari.Permissions.MANAGE_ROLES
    # | hikari.Permissions.MANAGE_MESSAGES
    | hikari.Permissions.READ_MESSAGE_HISTORY  # Needed to find own old messages
    | hikari.Permissions.ADD_REACTIONS
    | hikari.Permissions.KICK_MEMBERS
    | hikari.Permissions.USE_APPLICATION_COMMANDS
).value
CLIENT_ID = environ["CLIENT_ID"]
OAUTH_URL = "https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&permissions={OAUTH_PERMISSIONS}&scope=applications.commands%20bot"
INTENTS = (
    hikari.Intents.GUILD_MESSAGES
    | hikari.Intents.GUILD_MESSAGE_REACTIONS
    | hikari.Intents.GUILDS
    | hikari.Intents.GUILD_MEMBERS
    | hikari.Intents.GUILD_EMOJIS
)

if "TEST_GUILDS" in environ:
    TEST_GUILDS = [int(x) for x in environ["TEST_GUILDS"].split(",")]
else:
    TEST_GUILDS = []


class DragonpawBot(lightbulb.BotApp):
    def __init__(self):
        super().__init__(
            token=environ["BOT_TOKEN"],
            default_enabled_guilds=TEST_GUILDS,
            intents=INTENTS,
            force_color=True,
        )
        self._state: dict[hikari.Snowflake, structs.GuildState] = {}
        self.user_id: hikari.Snowflake | None

    def state(self, guild_id: hikari.Snowflake) -> structs.GuildState | None:
        # If we don't have a state in-memory, maybe there is one on disk?
        if guild_id not in self._state:
            state = state_load_yaml(guild_id=guild_id)
            if state:
                # If that returned a state, cache it.
                self._state[guild_id] = state

        # And return whatever is cached, if any...
        return self._state.get(guild_id)

    def state_update(self, state: structs.GuildState):
        self._state[state.id] = state
        state_save_yaml(state=state)


bot = DragonpawBot()

# ---------------------------------------------------------------------------- #
#                                 File handling                                #
# ---------------------------------------------------------------------------- #


def state_path(guild_id: hikari.Snowflake, extention="toml"):
    return Path(STATE_DIR, str(guild_id) + "." + extention)


def state_save_pickle(state: structs.GuildState):
    filename = state_path(state.id, extention="pickle")
    logger.info("G=%r Saving state to: %s", state.name, filename)
    with safer.open(filename, "wb") as f:
        pickle.dump(obj=state.model_dump(), file=f)


def state_load_pickle(guild_id: hikari.Snowflake) -> structs.GuildState | None:
    filename = state_path(guild_id=guild_id, extention="pickle")

    if not filename.exists():
        logger.debug("No state file for guild: %d", guild_id)
        return None

    logger.debug("Loading state from: %s", filename)
    try:
        with safer.open(filename, "rb") as f:
            return structs.GuildState.model_validate(pickle.load(f))
    except Exception as e:
        logger.exception("Error loading file: %r", e)
        return None


def _state_to_yaml_dict(state: structs.GuildState) -> dict[str, Any]:
    """Convert a GuildState to a plain dict suitable for YAML serialization."""
    data = state.model_dump(mode="json")

    # Convert role_emojis from list-of-pairs (Pydantic tuple-key dump) to nested dict
    raw_emojis = data.pop("role_emojis", {})
    nested: dict[int, dict[str, Any]] = {}
    for key, value in raw_emojis.items():
        # Pydantic dumps tuple keys as "(<msg_id>, '<emoji>')" strings in JSON mode.
        # But model_dump(mode="json") with tuple keys actually gives us the tuples
        # as string repr. We need to work with the original state instead.
        pass  # handled below

    # Work from the original model to get clean tuple keys
    nested = {}
    for (msg_id, emoji), opt_state in state.role_emojis.items():
        mid = int(msg_id)
        if mid not in nested:
            nested[mid] = {}
        nested[mid][emoji] = opt_state.model_dump(mode="json")
    data["role_emojis"] = nested

    # Coerce role_names keys to int (Pydantic JSON mode turns Snowflake to str)
    data["role_names"] = {int(k): v for k, v in data["role_names"].items()}

    return data


def _yaml_dict_to_state(data: dict[str, Any]) -> structs.GuildState:
    """Convert a YAML-loaded dict back into a GuildState."""
    # Reconstruct role_emojis as tuple-keyed dict
    nested_emojis = data.pop("role_emojis", {})
    role_emojis: dict[tuple[hikari.Snowflake, str], structs.RoleMenuOptionState] = {}
    for msg_id_str, emoji_map in nested_emojis.items():
        msg_id = hikari.Snowflake(msg_id_str)
        for emoji, opt_data in emoji_map.items():
            opt_data["add_role_id"] = hikari.Snowflake(opt_data["add_role_id"])
            opt_data["remove_role_ids"] = [
                hikari.Snowflake(r) for r in opt_data["remove_role_ids"]
            ]
            role_emojis[(msg_id, emoji)] = structs.RoleMenuOptionState.model_validate(
                opt_data
            )
    data["role_emojis"] = role_emojis

    # Reconstruct Snowflake types for role_names keys and scalar ID fields
    data["role_names"] = {
        hikari.Snowflake(k): v for k, v in data.get("role_names", {}).items()
    }
    data["id"] = hikari.Snowflake(data["id"])

    for field in (
        "lobby_role_id",
        "lobby_channel_id",
        "lobby_rules_message_id",
        "role_channel_id",
        "log_channel_id",
    ):
        if data.get(field) is not None:
            data[field] = hikari.Snowflake(data[field])

    return structs.GuildState.model_validate(data)


def state_save_yaml(state: structs.GuildState) -> None:
    filename = state_path(state.id, extention="yaml")
    logger.info("G=%r Saving state to: %s", state.name, filename)
    data = _state_to_yaml_dict(state)
    with safer.open(filename, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def state_load_yaml(guild_id: hikari.Snowflake) -> structs.GuildState | None:
    yaml_file = state_path(guild_id=guild_id, extention="yaml")

    if yaml_file.exists():
        logger.debug("Loading state from: %s", yaml_file)
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            return _yaml_dict_to_state(data)
        except Exception as e:
            logger.exception("Error loading YAML state: %r", e)
            return None

    # Auto-migrate from pickle if it exists
    pickle_file = state_path(guild_id=guild_id, extention="pickle")
    if pickle_file.exists():
        logger.info("Migrating state from pickle to YAML: %s", pickle_file)
        state = state_load_pickle(guild_id=guild_id)
        if state:
            state_save_yaml(state)
            pickle_file.unlink()
            return state

    logger.debug("No state file for guild: %d", guild_id)
    return None


# ---------------------------------------------------------------------------- #
#                                   Handlers                                   #
# ---------------------------------------------------------------------------- #


@bot.listen()
async def on_ready(event: hikari.ShardReadyEvent) -> None:
    """Post-initialization for the bot."""
    logger.info("Connected to Discord as %r", event.my_user)
    logger.info(
        "Use this URL to add this bot to a server: %s",
        OAUTH_URL.format(CLIENT_ID=CLIENT_ID, OAUTH_PERMISSIONS=OAUTH_PERMISSIONS),
    )
    bot.user_id = event.my_user.id

    flags = event.application_flags
    if (
        INTENTS & hikari.Intents.GUILD_MEMBERS
        and not flags & hikari.ApplicationFlags.GUILD_MEMBERS_INTENT
    ):
        logger.warning(
            "GUILD_MEMBERS intent is requested but NOT enabled in the "
            "Discord Developer Portal. Lobby welcome messages and member "
            "join events will silently fail. Enable it under: "
            "Bot > Privileged Gateway Intents > Server Members Intent"
        )


@bot.listen()
async def on_guild_available(event: hikari.GuildAvailableEvent):
    state = bot.state(guild_id=event.guild_id)
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
@lightbulb.add_checks(lightbulb.has_guild_permissions(hikari.Permissions.MANAGE_ROLES))
@lightbulb.option("url", "Link to the config you wish to use")
@lightbulb.command(
    "config",
    description="Configure Dragonpaw Bot via a url to a TOML file.",
    ephemeral=True,
)
@lightbulb.implements(lightbulb.SlashCommand)
async def config(ctx: lightbulb.Context) -> None:
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


def config_parse_toml(guild: hikari.Guild, text: str) -> structs.GuildConfig:
    logger.info("G=%r Loading TOML config for guild: %r", guild.name, guild)

    data = tomllib.loads(text)
    return structs.GuildConfig.model_validate(data)


async def configure_guild(bot: DragonpawBot, guild: hikari.Guild, url: str) -> None:
    """Load the config for a guild and start setting up everything there."""

    if url.startswith("https://gist.github.com"):
        config_text = await http.get_gist(url)
    else:
        config_text = await http.get_text(url)
    try:
        config = config_parse_toml(guild=guild, text=config_text)
    except tomllib.TOMLDecodeError as e:
        logger.error("Error parsing TOML file: %s", e)
        await utils.report_errors(bot=bot, guild_id=guild.id, error=str(e))
        return

    role_map = await utils.guild_roles(bot=bot, guild=guild)

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
        errors = await configure_role_menus(
            bot=bot,
            guild=guild,
            config=config.roles,
            state=state,
            role_map=role_map,
        )
        for err in errors:
            logger.error("Error setting up role menus: %r", err)
            await utils.report_errors(bot=bot, guild_id=guild.id, error=err)
    else:
        logger.debug("No roles menus")

    if config.lobby:
        errors = await configure_lobby(
            bot=bot,
            guild=guild,
            config=config.lobby,
            state=state,
            role_map=role_map,
        )
        for err in errors:
            logger.error("Error setting up lobby: %r", err)
            await utils.report_errors(bot=bot, guild_id=guild.id, error=err)
    else:
        logger.debug("No lobby.")

    # logger.debug("Final state: %r", state)
    bot.state_update(state)
    logger.info("G=%r Configured guild.", guild.name)


bot.load_extensions("dragonpaw_bot.plugins.lobby")
bot.load_extensions("dragonpaw_bot.plugins.role_menus")
