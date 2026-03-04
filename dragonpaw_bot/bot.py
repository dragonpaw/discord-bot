#!/usr/bin/env python
import asyncio
import datetime
import logging
import pickle
import tomllib
from os import environ
from pathlib import Path
from typing import Any

import hikari
import lightbulb
import safer
import uvloop
import yaml

from dragonpaw_bot import http, structs, utils
from dragonpaw_bot.plugins.birthdays import INTERACTION_HANDLERS as birthday_handlers
from dragonpaw_bot.plugins.birthdays import MODAL_HANDLERS as birthday_modal_handlers
from dragonpaw_bot.plugins.lobby import INTERACTION_HANDLERS as lobby_handlers
from dragonpaw_bot.plugins.lobby import configure_lobby
from dragonpaw_bot.plugins.role_menus import INTERACTION_HANDLERS as role_menu_handlers
from dragonpaw_bot.plugins.role_menus import configure_role_menus
from dragonpaw_bot.plugins.subday import INTERACTION_HANDLERS as subday_handlers
from dragonpaw_bot.utils import InteractionHandler, ModalHandler

logging.getLogger("dragonpaw_bot").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

# Interaction dispatch table: (prefix, handler, error_label).
# Sorted longest-prefix-first so "subday_cfg_role:" matches before "subday_cfg:".
_INTERACTION_ROUTES: list[tuple[str, InteractionHandler, str]] = sorted(
    [
        *((p, h, "processing your agreement") for p, h in lobby_handlers.items()),
        *((p, h, "processing your request") for p, h in subday_handlers.items()),
        *((p, h, "processing your request") for p, h in birthday_handlers.items()),
        *((p, h, "updating your roles") for p, h in role_menu_handlers.items()),
    ],
    key=lambda r: len(r[0]),
    reverse=True,
)

_MODAL_ROUTES: list[tuple[str, ModalHandler, str]] = sorted(
    [
        *(
            (p, h, "processing your request")
            for p, h in birthday_modal_handlers.items()
        ),
    ],
    key=lambda r: len(r[0]),
    reverse=True,
)

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT_DIR / "state"

OAUTH_PERMISSIONS = (
    hikari.Permissions.SEND_MESSAGES
    | hikari.Permissions.MANAGE_ROLES
    | hikari.Permissions.READ_MESSAGE_HISTORY  # Needed to find own old messages
    | hikari.Permissions.KICK_MEMBERS
    | hikari.Permissions.USE_APPLICATION_COMMANDS
).value
CLIENT_ID = environ["CLIENT_ID"]
OAUTH_URL = "https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&permissions={OAUTH_PERMISSIONS}&scope=applications.commands%20bot"
INTENTS = (
    hikari.Intents.GUILD_MESSAGES
    | hikari.Intents.GUILDS
    | hikari.Intents.GUILD_MEMBERS
    | hikari.Intents.GUILD_EMOJIS
)

if "TEST_GUILDS" in environ:
    TEST_GUILDS = [int(x) for x in environ["TEST_GUILDS"].split(",")]
else:
    TEST_GUILDS = []


class DragonpawBot(hikari.GatewayBot):
    def __init__(self):
        super().__init__(
            token=environ["BOT_TOKEN"],
            intents=INTENTS,
            force_color=True,
        )
        self._state: dict[hikari.Snowflake, structs.GuildState] = {}
        self.user_id: hikari.Snowflake | None = None
        logger.info("TEST_GUILDS=%r", TEST_GUILDS)

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
client = lightbulb.client_from_app(bot, default_enabled_guilds=TEST_GUILDS)

# Register bot in DI so tasks/commands can access it
registry = client.di.registry_for(lightbulb.di.Contexts.DEFAULT)
registry.register_value(hikari.GatewayBot, bot)
registry.register_value(DragonpawBot, bot)

# ---------------------------------------------------------------------------- #
#                                 File handling                                #
# ---------------------------------------------------------------------------- #


def state_path(guild_id: hikari.Snowflake, extension="toml"):
    return Path(STATE_DIR, str(guild_id) + "." + extension)


def state_save_pickle(state: structs.GuildState):
    filename = state_path(state.id, extension="pickle")
    logger.info("G=%r Saving state to: %s", state.name, filename)
    with safer.open(filename, "wb") as f:
        pickle.dump(obj=state.model_dump(), file=f)


def state_load_pickle(guild_id: hikari.Snowflake) -> structs.GuildState | None:
    filename = state_path(guild_id=guild_id, extension="pickle")

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
    return state.model_dump(mode="json")


def _yaml_dict_to_state(data: dict[str, Any]) -> structs.GuildState:
    """Convert a YAML-loaded dict back into a GuildState."""
    # Strip legacy role menu fields that now live in per-guild role_menus state
    data.pop("role_emojis", None)
    data.pop("role_names", None)
    data.pop("role_channel_id", None)

    data["id"] = hikari.Snowflake(data["id"])

    for field in (
        "lobby_role_id",
        "lobby_channel_id",
        "lobby_rules_message_id",
        "log_channel_id",
    ):
        if data.get(field) is not None:
            data[field] = hikari.Snowflake(data[field])

    return structs.GuildState.model_validate(data)


def state_save_yaml(state: structs.GuildState) -> None:
    filename = state_path(state.id, extension="yaml")
    logger.info("G=%r Saving state to: %s", state.name, filename)
    data = _state_to_yaml_dict(state)
    with safer.open(filename, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def state_load_yaml(guild_id: hikari.Snowflake) -> structs.GuildState | None:
    yaml_file = state_path(guild_id=guild_id, extension="yaml")

    if yaml_file.exists():
        logger.debug("Loading state from: %s", yaml_file)
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            return _yaml_dict_to_state(data)
        except Exception as e:
            logger.exception("Error loading YAML state: %r", e)
            logger.warning("Deleting corrupt state file: %s", yaml_file)
            yaml_file.unlink()
            return None

    # Auto-migrate from pickle if it exists
    pickle_file = state_path(guild_id=guild_id, extension="pickle")
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


@bot.listen(hikari.ShardReadyEvent)
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


@bot.listen(hikari.GuildAvailableEvent)
async def on_guild_available(event: hikari.GuildAvailableEvent):
    state = bot.state(guild_id=event.guild_id)
    if state:
        logger.info("G=%r State loaded from disk, resuming services", state.name)
    else:
        guild = event.get_guild()
        name = (guild and guild.name) or event.guild_id
        logger.info("G=%r No state found, so nothing to do.", name)


@bot.listen(hikari.GuildJoinEvent)
async def on_guild_join(event: hikari.GuildJoinEvent):
    guild = await bot.rest.fetch_guild(guild=event.guild_id)
    logger.info("G=%r Joined server.", guild.name)


# ---------------------------------------------------------------------------- #
#                                   Commands                                   #
# ---------------------------------------------------------------------------- #

loader = lightbulb.Loader()


roles_group = lightbulb.Group("roles", "Role menu management")
loader.command(roles_group)


@roles_group.register
class RolesConfig(
    lightbulb.SlashCommand,
    name="config",
    description="Configure Dragonpaw Bot via a url to a TOML file.",
    hooks=[lightbulb.prefab.has_permissions(hikari.Permissions.MANAGE_ROLES)],
):
    url = lightbulb.string("url", "Link to the config you wish to use")

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            logger.error("Interaction without a guild?!: %r", ctx)
            return

        await ctx.respond("Config loading now...", flags=hikari.MessageFlag.EPHEMERAL)

        g = await bot.rest.fetch_guild(guild=ctx.guild_id)
        logger.info("G=%r Setting up guild with file %r", g.name, self.url)
        errors = await configure_guild(bot=bot, guild=g, url=self.url)

        if errors:
            error_lines = "\n".join(f"- {e}" for e in errors)
            await ctx.respond(
                f"⚠️ **Config loaded with warnings:**\n{error_lines}",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
        else:
            await ctx.respond(
                "✅ Config loaded successfully.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )


@loader.command
class Logging(
    lightbulb.SlashCommand,
    name="logging",
    description="Set or clear the bot's log channel for this server.",
    hooks=[lightbulb.prefab.has_permissions(hikari.Permissions.MANAGE_GUILD)],
):
    channel = lightbulb.channel(
        "channel", "Channel for bot logs (omit to clear)", default=None
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            logger.error("Interaction without a guild?!: %r", ctx)
            return

        guild = await bot.rest.fetch_guild(guild=ctx.guild_id)
        state = bot.state(ctx.guild_id)
        if not state:
            state = structs.GuildState(
                id=ctx.guild_id,
                name=guild.name,
                config_url="",
                config_last=datetime.datetime.now(),
            )

        if self.channel is not None:
            state.log_channel_id = self.channel.id
            bot.state_update(state)
            logger.info(
                "G=%r U=%r: Set log channel to #%s",
                guild.name,
                ctx.user.username,
                self.channel.name,
            )
            await ctx.respond(
                f"Log channel set to <#{self.channel.id}>.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
        else:
            state.log_channel_id = None
            bot.state_update(state)
            logger.info(
                "G=%r U=%r: Cleared log channel",
                guild.name,
                ctx.user.username,
            )
            await ctx.respond(
                "Log channel cleared.", flags=hikari.MessageFlag.EPHEMERAL
            )


# ---------------------------------------------------------------------------- #
#                                Config handling                               #
# ---------------------------------------------------------------------------- #


def config_parse_toml(guild: hikari.Guild, text: str) -> structs.GuildConfig:
    logger.info("G=%r Loading TOML config for guild: %r", guild.name, guild)

    data = tomllib.loads(text)
    return structs.GuildConfig.model_validate(data)


async def configure_guild(
    bot: DragonpawBot, guild: hikari.Guild, url: str
) -> list[str]:
    """Load the config for a guild and start setting up everything there.

    Returns a list of warning/error messages for the caller to display.
    """
    all_errors: list[str] = []

    if url.startswith("https://gist.github.com"):
        config_text = await http.get_gist(url)
    else:
        config_text = await http.get_text(url)
    try:
        config = config_parse_toml(guild=guild, text=config_text)
    except tomllib.TOMLDecodeError as e:
        logger.error("Error parsing TOML file: %s", e)
        await utils.log_to_guild(bot, guild.id, f"🤯 **Config error:** {e}")
        return [f"Config error: {e}"]

    role_map = await utils.guild_roles(bot=bot, guild=guild)

    old_state = bot.state(guild.id)
    state = structs.GuildState(
        id=guild.id,
        name=guild.name,
        config_url=url,
        config_last=datetime.datetime.now(),
        log_channel_id=old_state.log_channel_id if old_state else None,
    )

    # Start setting up the guild
    if config.roles:
        errors = await configure_role_menus(
            bot=bot,
            guild=guild,
            config=config.roles,
            role_map=role_map,
        )
        for err in errors:
            logger.error("Error setting up role menus: %r", err)
            await utils.log_to_guild(bot, guild.id, f"🤯 **Role menu error:** {err}")
        all_errors.extend(errors)
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
            await utils.log_to_guild(bot, guild.id, f"🤯 **Lobby error:** {err}")
        all_errors.extend(errors)
    else:
        logger.debug("No lobby.")

    bot.state_update(state)
    logger.info("G=%r Configured guild.", guild.name)
    return all_errors


async def _respond_interaction_error(
    interaction: hikari.ComponentInteraction | hikari.ModalInteraction, message: str
) -> None:
    """Try to send an ephemeral error response; ignore if the interaction expired."""
    try:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content=message,
            flags=hikari.MessageFlag.EPHEMERAL,
        )
    except hikari.NotFoundError:
        pass  # Interaction expired
    except hikari.HTTPError:
        logger.warning(
            "Failed to send error response for interaction custom_id=%r",
            interaction.custom_id,
        )


@bot.listen(hikari.InteractionCreateEvent)
async def on_component_interaction(event: hikari.InteractionCreateEvent) -> None:
    """Central dispatcher for component and modal interactions.

    Routes to handlers by prefix match from _INTERACTION_ROUTES / _MODAL_ROUTES.
    Unmatched interactions are logged as errors.
    """
    interaction = event.interaction

    if isinstance(interaction, hikari.ComponentInteraction):
        routes = _INTERACTION_ROUTES
    elif isinstance(interaction, hikari.ModalInteraction):
        routes = _MODAL_ROUTES
    else:
        return

    cid = interaction.custom_id
    kind = "Modal" if isinstance(interaction, hikari.ModalInteraction) else "Component"

    logger.debug(
        "%s interaction: custom_id=%r user=%r guild=%r",
        kind,
        cid,
        interaction.user.username,
        interaction.guild_id,
    )

    for prefix, handler, error_label in routes:
        if cid.startswith(prefix):
            try:
                await handler(interaction)  # type: ignore[arg-type]
            except Exception:
                logger.exception(
                    "Error handling interaction: custom_id=%r user=%r",
                    cid,
                    interaction.user.username,
                )
                await _respond_interaction_error(
                    interaction, f"An error occurred {error_label}."
                )
            return

    logger.error(
        "Unhandled %s interaction: custom_id=%r user=%r guild=%r",
        kind.lower(),
        cid,
        interaction.user.username,
        interaction.guild_id,
    )


@bot.listen(hikari.StartingEvent)
async def on_starting(_: hikari.StartingEvent) -> None:
    await loader.add_to_client(client)
    await client.load_extensions(
        "dragonpaw_bot.plugins.lobby",
        "dragonpaw_bot.plugins.role_menus",
        "dragonpaw_bot.plugins.subday",
        "dragonpaw_bot.plugins.birthdays",
    )
    await client.start()
