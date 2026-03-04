# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

**`bot.py`** — Core module. Defines `DragonpawBot` (subclass of `hikari.GatewayBot`) with state management, plus a `lightbulb.Client` created via `client_from_app()`. The `/roles config` and `/logging` slash commands are defined here. Extensions are loaded asynchronously on `StartingEvent`.

**`structs.py`** — All data models using Pydantic v2. Two layers:

- **Config models** (`GuildConfig`, `LobbyConfig`) — parsed from TOML config files
- **State models** (`GuildState`) — runtime state persisted as YAML files

**Extensions** (loaded via `client.load_extensions` during `StartingEvent`):

- **`plugins/role_menus/`** — Posts embed menus with text select menus (dropdowns) in a designated channel. Handles component interactions to assign/remove Discord roles. Supports single-select and multi-select menus. See `plugins/role_menus/CLAUDE.md` for details. Multi-file plugin with models, commands, state persistence, and constants.
- **`plugins/lobby.py`** — Handles new member joins: auto-assigns a role, posts welcome messages, and optionally shows server rules with an "I agree" button that removes the lobby role.
- **`plugins/subday/`** — 52-week guided journal program ("Where I am Led"). See `plugins/subday/CLAUDE.md` for details. Multi-file plugin with models, commands, cron scheduler, prompt parser, and state persistence.
- **`plugins/birthdays/`** — Birthday tracking with announcements and wishlists. See `plugins/birthdays/CLAUDE.md` for details. Multi-file plugin with models, commands, daily cron task, and state persistence.

**`utils.py`** — Discord helpers: deleting bot messages, looking up channels/roles/emojis by name, checking member roles, and `log_to_guild()` for sending notifications to a guild's configured log channel.

**`http.py`** — Async HTTP client for fetching TOML configs, with special GitHub Gist URL handling.

**`colors.py`** — Solarized color constants and a `rainbow()` helper using `palettable` for generating embed color palettes.

**Config flow:** Server admins use the `/roles config` slash command with a URL to a role-menu TOML file. The bot fetches and parses it directly into a `RolesConfig`, sets up role menus, then persists `GuildState` to disk as YAML. The `/logging` command sets or clears the guild's log channel (`GuildState.log_channel_id`), which is preserved across `/roles config` reloads.

**Guild logging:** `utils.log_to_guild()` sends plain-text notifications to the guild's configured log channel. All plugins use this for auditable events (errors, completions, config changes, signups, removals). Silently skips if no log channel is configured. Each message should have a unique leading emoji.

**State serialization note:** Role menu state is now persisted separately in `state/role_menus_{guild_id}.yaml`. The main `GuildState` YAML is straightforward Pydantic JSON-mode serialization. Legacy YAML files containing `role_emojis`/`role_names`/`role_channel_id` are automatically stripped on load.

## Key Conventions

- Uses `uvloop` as the async event loop
- **Discord interaction timeout:** Discord gives 3 seconds to respond to an interaction before it expires. Always call `ctx.respond()` (or `interaction.create_initial_response()`) **before** any slow work like sending DMs, posting to channels, assigning roles, or calling `log_to_guild()`. Do the fast stuff (state save, build response), respond, then do async work after.
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
- Logging follows the pattern `logger.info("G=%r U=%r: ...", guild_name, username, ...)`
- Debug logging is enabled for the `dragonpaw_bot` logger (`logging.DEBUG`)
- Python version target: 3.13
- Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed)
- Ruff lint rules: isort (`I`) and pylint (`PL`) enabled in addition to defaults
- New features should be implemented as extensions under `dragonpaw_bot/plugins/`
- Plugins should include comprehensive logging at appropriate levels:
  - **Info**: user actions (signups, completions, config changes)
  - **Debug**: internal details (state loads, DM delivery, tick timing)
  - **Warning**: missing resources (channels, roles), DM failures, corrupt state
  - All commands should log permission denials
- Shared helpers belong in `utils.py` (e.g. `member_has_role`, `guild_role_by_name`, `guild_channel_by_name`)
- Plugin-specific docs: Each plugin includes a `CLAUDE.md` file in its directory describing functionality, architecture, and configuration. This is the single source of truth for what the plugin does. When adding, changing, or removing features from a plugin, always update its CLAUDE.md file to reflect the current state.
- State is persisted as YAML files in `state/` using `safer` for atomic writes
- All Python files are UTF-8 (with `# -*- coding: utf-8 -*-` header). Use literal emoji characters (`📖`, `✅`) directly in source, never Unicode escapes (`\U0001f4d6`, `\u2705`).
- Type checking uses `ty` (not mypy): `uv run ty check dragonpaw_bot/`
