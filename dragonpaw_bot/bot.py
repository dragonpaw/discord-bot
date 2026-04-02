#!/usr/bin/env python
import asyncio
import contextlib
import datetime
import pickle
from os import environ
from pathlib import Path
from typing import Any

import hikari
import lightbulb
import safer
import structlog
import uvloop
import yaml

from dragonpaw_bot import structs
from dragonpaw_bot.context import GuildContext, NotConfigAdmin, guild_owner_only
from dragonpaw_bot.logging import configure_logging
from dragonpaw_bot.plugins.birthdays import INTERACTION_HANDLERS as birthday_handlers
from dragonpaw_bot.plugins.birthdays import MODAL_HANDLERS as birthday_modal_handlers
from dragonpaw_bot.plugins.birthdays import config as birthday_config
from dragonpaw_bot.plugins.channel_cleanup import config as cleanup_config
from dragonpaw_bot.plugins.intros import config as intros_config
from dragonpaw_bot.plugins.media_channels import config as media_config
from dragonpaw_bot.plugins.role_menus import INTERACTION_HANDLERS as role_menu_handlers
from dragonpaw_bot.plugins.role_menus import config as roles_config
from dragonpaw_bot.plugins.subday import INTERACTION_HANDLERS as subday_handlers
from dragonpaw_bot.plugins.subday import config as subday_config
from dragonpaw_bot.plugins.tickets import INTERACTION_HANDLERS as tickets_handlers
from dragonpaw_bot.plugins.tickets import MODAL_HANDLERS as tickets_modal_handlers
from dragonpaw_bot.plugins.tickets import config as tickets_config
from dragonpaw_bot.plugins.validation import INTERACTION_HANDLERS as validation_handlers
from dragonpaw_bot.plugins.validation import MODAL_HANDLERS as validation_modal_handlers
from dragonpaw_bot.plugins.validation import config as validation_config
from dragonpaw_bot.utils import InteractionHandler, ModalHandler

configure_logging()
logger = structlog.get_logger(__name__)

# Interaction dispatch table: (prefix, handler, plugin_name).
# Sorted longest-prefix-first so "subday_cfg_role:" matches before "subday_cfg:".
_INTERACTION_ROUTES: list[tuple[str, InteractionHandler, str]] = sorted(
    [
        *((p, h, "subday") for p, h in subday_handlers.items()),
        *((p, h, "birthdays") for p, h in birthday_handlers.items()),
        *((p, h, "role_menus") for p, h in role_menu_handlers.items()),
        *((p, h, "tickets") for p, h in tickets_handlers.items()),
        *((p, h, "validation") for p, h in validation_handlers.items()),
    ],
    key=lambda r: len(r[0]),
    reverse=True,
)

_MODAL_ROUTES: list[tuple[str, ModalHandler, str]] = sorted(
    [
        *((p, h, "birthdays") for p, h in birthday_modal_handlers.items()),
        *((p, h, "tickets") for p, h in tickets_modal_handlers.items()),
        *((p, h, "validation") for p, h in validation_modal_handlers.items()),
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
    | hikari.Permissions.MANAGE_MESSAGES
    | hikari.Permissions.READ_MESSAGE_HISTORY  # Needed to find own old messages
    | hikari.Permissions.KICK_MEMBERS
    | hikari.Permissions.USE_APPLICATION_COMMANDS
).value
CLIENT_ID = environ["CLIENT_ID"]


def _read_build_tag() -> str:
    """Read build tag from .tag file (baked in at docker build), env var, or default."""
    tag_file = Path(__file__).parent.parent / "BUILD_TAG"
    if tag_file.is_file():
        return tag_file.read_text().strip() or "dev"
    return environ.get("BUILD_TAG", "dev")


BUILD_TAG = _read_build_tag()
OAUTH_URL = "https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&permissions={OAUTH_PERMISSIONS}&scope=applications.commands%20bot"
INTENTS = (
    hikari.Intents.GUILD_MESSAGES
    | hikari.Intents.MESSAGE_CONTENT
    | hikari.Intents.GUILDS
    | hikari.Intents.GUILD_MEMBERS
    | hikari.Intents.GUILD_EMOJIS
    | hikari.Intents.GUILD_MESSAGE_REACTIONS
    | hikari.Intents.GUILD_VOICE_STATES
    | hikari.Intents.DM_MESSAGES
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
        self.application_flags: hikari.ApplicationFlags | None = None
        logger.info("Starting bot", build=BUILD_TAG, test_guilds=TEST_GUILDS)

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


@client.error_handler
async def on_command_error(
    exc: lightbulb.exceptions.ExecutionPipelineFailedException,
) -> bool:
    if any(isinstance(c, NotConfigAdmin) for c in exc.causes):
        await exc.context.respond(
            "*guards the treasure* 🐉 You need **Manage Server** permission to use this command!",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return True
    for cause in exc.causes:
        logger.exception(
            "Command failed",
            command=exc.context.command_data.qualified_name,
            exc_info=cause,
        )
    return False  # Let lightbulb continue its default handling


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
    logger.info("Saving state", guild=state.name, path=str(filename))
    with safer.open(filename, "wb") as f:
        pickle.dump(obj=state.model_dump(), file=f)


def state_load_pickle(guild_id: hikari.Snowflake) -> structs.GuildState | None:
    filename = state_path(guild_id=guild_id, extension="pickle")

    if not filename.exists():
        logger.debug("No state file for guild", guild_id=guild_id)
        return None

    logger.debug("Loading state", path=str(filename))
    try:
        with safer.open(filename, "rb") as f:
            return structs.GuildState.model_validate(pickle.load(f))
    except Exception:
        logger.exception("Error loading file", path=str(filename))
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

    if data.get("log_channel_id") is not None:
        data["log_channel_id"] = hikari.Snowflake(data["log_channel_id"])

    if data.get("general_channel_id") is not None:
        data["general_channel_id"] = hikari.Snowflake(data["general_channel_id"])

    # Strip legacy lobby fields
    for field in (
        "lobby_role_id",
        "lobby_welcome_message",
        "lobby_channel_id",
        "lobby_click_for_rules",
        "lobby_kick_days",
        "lobby_rules",
        "lobby_rules_message_id",
    ):
        data.pop(field, None)

    return structs.GuildState.model_validate(data)


def state_save_yaml(state: structs.GuildState) -> None:
    filename = state_path(state.id, extension="yaml")
    logger.info("Saving state", guild=state.name, path=str(filename))
    data = _state_to_yaml_dict(state)
    with safer.open(filename, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def state_load_yaml(guild_id: hikari.Snowflake) -> structs.GuildState | None:
    yaml_file = state_path(guild_id=guild_id, extension="yaml")

    if yaml_file.exists():
        logger.debug("Loading state", path=str(yaml_file))
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            return _yaml_dict_to_state(data)
        except Exception:
            logger.exception("Error loading YAML state", path=str(yaml_file))
            logger.warning("Deleting corrupt state file", path=str(yaml_file))
            yaml_file.unlink()
            return None

    # Auto-migrate from pickle if it exists
    pickle_file = state_path(guild_id=guild_id, extension="pickle")
    if pickle_file.exists():
        logger.info("Migrating state from pickle to YAML", path=str(pickle_file))
        state = state_load_pickle(guild_id=guild_id)
        if state:
            state_save_yaml(state)
            pickle_file.unlink()
            return state

    logger.debug("No state file for guild", guild_id=guild_id)
    return None


# ---------------------------------------------------------------------------- #
#                                   Handlers                                   #
# ---------------------------------------------------------------------------- #


@bot.listen(hikari.ShardReadyEvent)
async def on_ready(event: hikari.ShardReadyEvent) -> None:
    """Post-initialization for the bot."""
    logger.info("Connected to Discord", user=str(event.my_user), build=BUILD_TAG)
    logger.info(
        "OAuth URL",
        url=OAUTH_URL.format(CLIENT_ID=CLIENT_ID, OAUTH_PERMISSIONS=OAUTH_PERMISSIONS),
    )
    bot.user_id = event.my_user.id
    bot.application_flags = event.application_flags

    flags = event.application_flags
    # Discord sets different flags for verified vs unverified bots:
    # - *_INTENT flags are set for verified bots (75+ guilds)
    # - *_INTENT_LIMITED flags are set for unverified bots
    # We need to check both to avoid false warnings.
    _MEMBERS_FLAGS = (
        hikari.ApplicationFlags.GUILD_MEMBERS_INTENT
        | hikari.ApplicationFlags.VERIFIED_FOR_GUILD_MEMBERS_INTENT
    )
    _CONTENT_FLAGS = (
        hikari.ApplicationFlags.MESSAGE_CONTENT_INTENT
        | hikari.ApplicationFlags.MESSAGE_CONTENT_INTENT_LIMITED
    )

    if INTENTS & hikari.Intents.GUILD_MEMBERS and not flags & _MEMBERS_FLAGS:
        logger.warning(
            "GUILD_MEMBERS intent is requested but NOT enabled in the "
            "Discord Developer Portal. Validation plugin member join/update "
            "events will silently fail. Enable it under: "
            "Bot > Privileged Gateway Intents > Server Members Intent"
        )

    if INTENTS & hikari.Intents.MESSAGE_CONTENT and not flags & _CONTENT_FLAGS:
        logger.warning(
            "MESSAGE_CONTENT intent is requested but NOT enabled in the "
            "Discord Developer Portal. Media channel enforcement will be "
            "unable to read message content and will fail to detect text-only posts. "
            "Enable it under: Bot > Privileged Gateway Intents > Message Content Intent"
        )


_BLOCKED_GUILDS = {915486296883990528}


@bot.listen(hikari.GuildAvailableEvent)
async def on_guild_available(event: hikari.GuildAvailableEvent):
    if int(event.guild_id) in _BLOCKED_GUILDS:
        logger.warning(
            "In blocked guild, leaving",
            guild=event.guild.name,
            guild_id=event.guild_id,
        )
        await bot.rest.leave_guild(event.guild_id)
        return
    state = bot.state(guild_id=event.guild_id)
    if state:
        logger.info("State loaded from disk, resuming services", guild=state.name)
    else:
        guild = event.get_guild()
        name = (guild and guild.name) or str(event.guild_id)
        logger.info("No state found, nothing to do", guild=name)


@bot.listen(hikari.GuildJoinEvent)
async def on_guild_join(event: hikari.GuildJoinEvent):
    guild = await bot.rest.fetch_guild(guild=event.guild_id)
    if int(event.guild_id) in _BLOCKED_GUILDS:
        logger.warning(
            "Joined blocked guild, leaving immediately",
            guild=guild.name,
            guild_id=event.guild_id,
        )
        await bot.rest.leave_guild(event.guild_id)
        return
    logger.info("Joined server", guild=guild.name)


@bot.listen(hikari.DMMessageCreateEvent)
async def on_dm_message(event: hikari.DMMessageCreateEvent) -> None:
    """Respond to DMs with a cute note to use slash commands instead."""
    if event.is_bot:
        return
    with contextlib.suppress(hikari.HTTPError):
        await event.message.respond(
            "*peeks out of cave* 🐉 Rawr! I don't really do DMs — "
            "try using my `/` slash commands in the server instead! 🐾"
        )


# ---------------------------------------------------------------------------- #
#                                   Commands                                   #
# ---------------------------------------------------------------------------- #

loader = lightbulb.Loader()

_config_group = lightbulb.Group("config", "Bot configuration")
_channels_sub = _config_group.subgroup("channels", "Channel settings")
_media_sub = _config_group.subgroup("media", "Media-only channel settings")
_cleanup_sub = _config_group.subgroup("cleanup", "Auto-expiry channel settings")
_intros_sub = _config_group.subgroup("intros", "Intro channel settings")
_subday_sub = _config_group.subgroup("subday", "SubDay journal program settings")
_birthday_sub = _config_group.subgroup("birthday", "Birthday tracking settings")
_roles_sub = _config_group.subgroup("roles", "Role menu settings")
_tickets_sub = _config_group.subgroup("tickets", "Help ticket settings")
_validation_sub = _config_group.subgroup("validation", "Member validation settings")


class SetLogChannel(
    lightbulb.SlashCommand,
    name="log",
    description="Set or clear the bot's log channel for this server.",
    hooks=[guild_owner_only],
):
    channel = lightbulb.channel(
        "channel", "Channel for bot logs (omit to clear)", default=None
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            logger.error("Interaction without a guild")
            return

        gc = GuildContext.from_ctx(ctx)
        guild = await gc.fetch_guild()
        state = gc.state()
        if not state:
            state = structs.GuildState(
                id=ctx.guild_id,
                name=guild.name,
                config_url="",
                config_last=datetime.datetime.now(tz=datetime.UTC),
            )

        if self.channel is not None:
            state.log_channel_id = self.channel.id
            bot.state_update(state)
            gc.logger.info("Set log channel", channel=self.channel.name)
            await ctx.respond(
                f"Log channel set to <#{self.channel.id}>.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
        else:
            state.log_channel_id = None
            bot.state_update(state)
            gc.logger.info("Cleared log channel")
            await ctx.respond(
                "Log channel cleared.", flags=hikari.MessageFlag.EPHEMERAL
            )


class SetGeneralChannel(
    lightbulb.SlashCommand,
    name="general",
    description="Set or clear the general chat channel for this server.",
    hooks=[guild_owner_only],
):
    channel = lightbulb.channel(
        "channel", "General chat channel (omit to clear)", default=None
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            logger.error("Interaction without a guild")
            return

        gc = GuildContext.from_ctx(ctx)
        guild = await gc.fetch_guild()
        state = gc.state()
        if not state:
            state = structs.GuildState(
                id=ctx.guild_id,
                name=guild.name,
                config_url="",
                config_last=datetime.datetime.now(tz=datetime.UTC),
            )

        if self.channel is not None:
            state.general_channel_id = self.channel.id
            bot.state_update(state)
            gc.logger.info("Set general chat channel", channel=self.channel.name)
            await ctx.respond(
                f"General chat channel set to <#{self.channel.id}>.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            await gc.log(
                f"⚙️ {ctx.user.mention} set the general chat channel to <#{self.channel.id}> 🐉"
            )
        else:
            state.general_channel_id = None
            bot.state_update(state)
            gc.logger.info("Cleared general chat channel")
            await ctx.respond(
                "General chat channel cleared.", flags=hikari.MessageFlag.EPHEMERAL
            )
            await gc.log(f"⚙️ {ctx.user.mention} cleared the general chat channel 🐉")


from dragonpaw_bot.plugins.activity import config as activity_config  # noqa: E402

_activity_sub = _config_group.subgroup("activity", "Activity tracker settings")

_channels_sub.register(SetLogChannel)
_channels_sub.register(SetGeneralChannel)
media_config.register(_media_sub)
cleanup_config.register(_cleanup_sub)
intros_config.register(_intros_sub)
subday_config.register(_subday_sub)
birthday_config.register(_birthday_sub)
roles_config.register(_roles_sub)
tickets_config.register(_tickets_sub)
validation_config.register(_validation_sub)
activity_config.register(_activity_sub)
loader.command(_config_group)


async def _respond_interaction_error(
    interaction: hikari.ComponentInteraction | hikari.ModalInteraction,
) -> None:
    """Try to send an ephemeral error response; ignore if the interaction expired."""
    try:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="An error occurred.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
    except hikari.NotFoundError:
        pass  # Interaction expired
    except hikari.HTTPError:
        logger.warning("Failed to send error response")


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
    kind = "modal" if isinstance(interaction, hikari.ModalInteraction) else "component"

    structlog.contextvars.clear_contextvars()
    cached_guild = (
        bot.cache.get_guild(interaction.guild_id) if interaction.guild_id else None
    )
    structlog.contextvars.bind_contextvars(
        guild=cached_guild.name if cached_guild else str(interaction.guild_id),
        user=interaction.member.display_name
        if interaction.member
        else interaction.user.username,
        custom_id=cid,
    )

    logger.debug("Interaction received", kind=kind)

    for prefix, handler, plugin_name in routes:
        if cid.startswith(prefix):
            structlog.contextvars.bind_contextvars(plugin=plugin_name)
            try:
                await handler(interaction)  # type: ignore[arg-type]
            except Exception:
                logger.exception("Error handling interaction")
                await _respond_interaction_error(interaction)
            return

    logger.error("Unhandled interaction", kind=kind)


@bot.listen(hikari.StartingEvent)
async def on_starting(_: hikari.StartingEvent) -> None:
    await loader.add_to_client(client)
    await client.load_extensions(
        "dragonpaw_bot.plugins.role_menus",
        "dragonpaw_bot.plugins.subday",
        "dragonpaw_bot.plugins.birthdays",
        "dragonpaw_bot.plugins.media_channels",
        "dragonpaw_bot.plugins.channel_cleanup",
        "dragonpaw_bot.plugins.intros",
        "dragonpaw_bot.plugins.tickets",
        "dragonpaw_bot.plugins.validation",
        "dragonpaw_bot.plugins.activity",
    )
    await client.start()

    # Log all registered cron tasks
    for task in sorted(client._tasks, key=lambda t: getattr(t._func, "__name__", "")):
        closure = getattr(task._trigger, "__closure__", None)
        if closure and hasattr(closure[0].cell_contents, "expressions"):
            cron_expr = " ".join(str(x) for x in closure[0].cell_contents.expressions)
            schedule = f"cron({cron_expr})"
        else:
            schedule = "unknown"
        logger.info(
            "Registered task",
            task=getattr(task._func, "__name__", repr(task)),
            schedule=schedule,
        )
