# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Tools

- **Context7 MCP** is available for looking up current API docs for any library (hikari, lightbulb, pydantic, etc.). Use it instead of guessing at APIs.

## Bot Personality

The bot's persona is a cute, enthusiastic little hungry dragon who loves to snack on messages. When writing user-facing copy (enforcement notices, status messages, etc.), lean into this — use playful, warm, dragon-themed language. The bot's avatar is a dragon riding a shark with lasers. Never be scolding or cold in user-facing messages.

**This applies to staff channel log messages too.** All `gc.log()` messages should be warm and on-voice — never dry or corporate. Use first-person ("I/me/my"), dragon emotes like `*snorts smoke*`, `*happy tail wag*`, `*nom*`, etc., and trail messages with 🐉 or 🐾 where fitting. Errors should still be clear and actionable, but written with personality.

## Project Overview

A Discord bot ("Dragonpaw Bot") built with Python using the **hikari** + **hikari-lightbulb v3** framework. It provides features for Discord servers: select-menu-based role assignment, a lobby/welcome system with optional click-through rules, and a 52-week guided journal program (SubDay).

## Build & Run Commands

- **Install dependencies:** `uv sync`
- **Run the bot:** `uv run python -m dragonpaw_bot`
- **Type checking:** `uv run ty check dragonpaw_bot/`
- **Linting:** `uv run ruff check dragonpaw_bot/`
- **Formatting:** `uv run ruff format dragonpaw_bot/`
- **Tests:** `uv run pytest`
- **Single test:** `uv run pytest tests/test_bot.py::test_name`

## Required Environment Variables

- `BOT_TOKEN` — Discord bot token
- `CLIENT_ID` — Discord application client ID
- `TEST_GUILDS` (optional) — Comma-separated guild IDs for slash command testing

## Architecture

**Entry point:** `dragonpaw_bot/__main__.py` → calls `bot.run()` from `bot.py`.

**`bot.py`** — Core module. Defines `DragonpawBot` (subclass of `hikari.GatewayBot`) with state management, plus a `lightbulb.Client` created via `client_from_app()`. The `/config` command group and `/config bot logging` command are defined here. Extensions are loaded asynchronously on `StartingEvent`.

**`structs.py`** — All data models using Pydantic v2. Two layers:

- **Config models** (`GuildConfig`, `LobbyConfig`) — parsed from TOML config files
- **State models** (`GuildState`) — runtime state persisted as YAML files

**Extensions** (loaded via `client.load_extensions` during `StartingEvent`):

- **`plugins/role_menus/`** — Posts embed menus with text select menus (dropdowns) in a designated channel. Handles component interactions to assign/remove Discord roles. Supports single-select and multi-select menus. See `plugins/role_menus/CLAUDE.md` for details. Multi-file plugin with models, commands, state persistence, and constants.
- **`plugins/lobby.py`** — Handles new member joins: auto-assigns a role, posts welcome messages, and optionally shows server rules with an "I agree" button that removes the lobby role.
- **`plugins/subday/`** — 52-week guided journal program ("Where I am Led"). See `plugins/subday/CLAUDE.md` for details. Multi-file plugin with models, commands, cron scheduler, prompt parser, and state persistence.
- **`plugins/birthdays/`** — Birthday tracking with announcements and wishlists. See `plugins/birthdays/CLAUDE.md` for details. Multi-file plugin with models, commands, daily cron task, and state persistence.
- **`plugins/media_channels/`** — Enforces media-only policy in configured channels; hourly cleanup cron. See `plugins/media_channels/CLAUDE.md` for details.
- **`plugins/channel_cleanup/`** — Auto-deletes old messages from configured channels via hourly cron. See `plugins/channel_cleanup/CLAUDE.md` for details.

**`/config` command group** — Defined in `bot.py` (main loader) and extended by each plugin's `config.py`. Each plugin that needs admin configuration exposes a `register(subgroup)` function in `plugin_dir/config.py`; `bot.py` imports these and wires them into the `/config` group. Subgroups: `/config bot` (logging, defined in `bot.py`), `/config media` (media channels), `/config cleanup` (channel cleanup), `/config subday` (SubDay journal), `/config birthday` (birthday tracking), `/config roles` (role menus).

**`duration.py`** — Shared `parse_duration_minutes()` and `format_duration()` helpers used by plugin config commands.

**`context.py`** — `GuildContext` and `ChannelContext` dataclasses that bundle bot + guild info for convenient access throughout plugins. `GuildContext` provides factory methods (`from_ctx`, `from_interaction`, `from_guild`), permission checks, and `gc.log()` for sending notifications to the guild's configured log channel. `ChannelContext` extends it with channel-level operations like `purge_old_messages()` and `delete_my_messages()`. Also contains standalone permission helpers (`member_has_role`, `has_permission`, `has_any_role_permission`), `check_channel_perms` (accepts optional `required` permission set — defaults to `CHANNEL_POST_PERMS`; use `CHANNEL_CLEANUP_PERMS` for cleanup channels), and `check_role_manageable` (checks bot can manage a role via permissions + hierarchy).

**`utils.py`** — Discord helpers: looking up channels/roles/emojis by name.

**`http.py`** — Async HTTP client for fetching TOML configs, with special GitHub Gist URL handling.

**`colors.py`** — Solarized color constants and a `rainbow()` helper using `palettable` for generating embed color palettes.

**Config flow:** Server admins use the `/config roles setup` slash command with a URL to a role-menu TOML file. The bot fetches and parses it directly into a `RolesConfig`, sets up role menus, then persists `GuildState` to disk as YAML. The `/config bot logging` command sets or clears the guild's log channel (`GuildState.log_channel_id`), which is preserved across `/config roles setup` reloads.

**Guild logging:** `gc.log()` (on `GuildContext`) sends plain-text notifications to the guild's configured log channel. All plugins use this for auditable events (errors, completions, config changes, signups, removals). Silently skips if no log channel is configured. Each message should have a unique leading emoji. Use first-person ("I/me/my") in bot-facing staff messages (dragon persona).

**Permission validation:** Config commands that set up channels or roles should validate permissions at setup time and warn the admin (but still save the config). Runtime permission errors in cron tasks should post actionable fix instructions to the guild log channel via `gc.log()`. Use `check_channel_perms` with the appropriate permission set and `check_role_manageable` for role hierarchy checks.

**Global error handler:** A lightbulb error handler on the client logs full stack traces for command failures via `logger.exception()`, then returns `False` to let lightbulb continue default handling.

**State serialization note:** Role menu state is now persisted separately in `state/role_menus_{guild_id}.yaml`. The main `GuildState` YAML is straightforward Pydantic JSON-mode serialization. Legacy YAML files containing `role_emojis`/`role_names`/`role_channel_id` are automatically stripped on load.

## Git Workflow

- Push directly to `main` — no feature branches.

## Key Conventions

- Uses `uvloop` as the async event loop
- **Discord interaction timeout:** Discord gives 3 seconds to respond to an interaction before it expires. Always call `ctx.respond()` (or `interaction.create_initial_response()`) **before** any slow work like sending DMs, posting to channels, assigning roles, or calling `gc.log()`. Do the fast stuff (state save, build response), respond, then do async work after.
- **lightbulb v3 patterns:**
  - Extensions use `lightbulb.Loader()` (not `Plugin`)
  - Commands are class-based, inheriting from `lightbulb.SlashCommand` with `@lightbulb.invoke` on the invoke method
  - Command groups use `lightbulb.Group()` with `@group.register` for subcommands
  - Options are class attributes: `user = lightbulb.user("user", "description")`
  - Checks use `hooks=[lightbulb.prefab.owner_only]` or `hooks=[lightbulb.prefab.has_permissions(...)]` in class params
  - Scheduled tasks use `@loader.task(lightbulb.crontrigger("..."))` with DI
  - Context API: `ctx.user` (not `ctx.author`), `ctx.client.app` to get the bot
  - Event listeners: `@loader.listener(hikari.EventType)`, access bot via `event.app`
- Pydantic v2 API (`model_validate`, `.model_dump()`)
- Logging uses **structlog** with structured keyword arguments: `logger.info("Event description", guild=name, user=display_name)`. Use `structlog.get_logger(__name__)` in each module. The central interaction dispatcher binds `guild`, `user`, `custom_id`, and `plugin` into contextvars — handlers called from it should omit those keys. Use `logger.bind(guild=..., user=...)` for scoped loggers in slash commands, cron tasks, and event listeners. When logging Discord users, use their guild display name (`member.display_name`) not their username or ID — display names are what server staff recognize.
- Debug logging is enabled for the `dragonpaw_bot` logger. Logging is configured in `dragonpaw_bot/logging.py`.
- Python version target: 3.13
- Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed)
- Ruff lint rules: `I`, `PL`, `UP`, `SIM`, `PERF`, `RUF`, `TRY`, `DTZ`, `TC` enabled; `RUF001`, `RUF002`, `TRY003` ignored
- New features should be implemented as extensions under `dragonpaw_bot/plugins/`
- Plugins should include comprehensive logging at appropriate levels:
  - **Info**: user actions (signups, completions, config changes)
  - **Debug**: internal details (state loads, DM delivery, tick timing)
  - **Warning**: missing resources (channels, roles), DM failures, corrupt state
  - All commands should log permission denials
- Shared helpers belong in `utils.py` (e.g. `member_has_role`, `guild_role_by_name`, `guild_channel_by_name`)
- Plugin-specific docs: Each plugin includes a `CLAUDE.md` file in its directory describing functionality, architecture, and configuration. This is the single source of truth for what the plugin does. When adding, changing, or removing features from a plugin, always update its CLAUDE.md file to reflect the current state.
- State is persisted as YAML files in `state/` using `safer` for atomic writes
- All Python files are UTF-8. Use literal emoji characters (`📖`, `✅`) directly in source, never Unicode escapes (`\U0001f4d6`, `\u2705`).
- Type checking uses `ty` (not mypy): `uv run ty check dragonpaw_bot/`
