# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Discord bot ("Dragonpaw Bot") built with Python using the **hikari** + **hikari-lightbulb** framework. It provides two main features for Discord servers: reaction-based role menus and a lobby/welcome system with optional click-through rules.

## Build & Run Commands

- **Install dependencies:** `uv sync`
- **Run the bot:** `uv run python -m dragonpaw_bot`
- **Type checking:** `uv run mypy dragonpaw_bot/`
- **Linting:** `uv run ruff check dragonpaw_bot/`
- **Formatting:** `uv run ruff format dragonpaw_bot/`
- **Tests:** `uv run pytest`
- **Single test:** `uv run pytest tests/test_bot.py::test_name`

## Required Environment Variables

- `BOT_TOKEN` — Discord bot token
- `CLIENT_ID` — Discord application client ID
- `TEST_GUILDS` (optional) — Comma-separated guild IDs for slash command testing

A `.env` file is loaded automatically via `python-dotenv`.

## Architecture

**Entry point:** `dragonpaw_bot/__main__.py` → calls `bot.run()` from `bot.py`.

**`bot.py`** — Core module. Defines `DragonpawBot` (subclass of `lightbulb.BotApp`), the `/config` slash command, guild state persistence (YAML files in `state/`), and config loading from remote TOML files (including GitHub Gists via `http.py`).

**`structs.py`** — All data models using Pydantic v2. Two layers:
- **Config models** (`GuildConfig`, `LobbyConfig`, `RolesConfig`, etc.) — parsed from TOML config files
- **State models** (`GuildState`, `RoleMenuOptionState`) — runtime state persisted as YAML files

**Plugins** (loaded via `bot.load_extensions`):
- **`plugins/role_menus.py`** — Posts embed menus with emoji reactions in a designated channel. Listens for reaction add/remove events to assign/remove Discord roles. Supports single-select menus (picking one removes others).
- **`plugins/lobby.py`** — Handles new member joins: auto-assigns a role, posts welcome messages, and optionally shows server rules with an "I agree" button that removes the lobby role.

**`utils.py`** — Discord helpers: deleting bot messages, looking up channels/roles/emojis by name, error reporting.

**`http.py`** — Async HTTP client for fetching TOML configs, with special GitHub Gist URL handling.

**Config flow:** Server admins use the `/config` slash command with a URL to a TOML file. The bot fetches and parses it, sets up role menus and lobby, then persists `GuildState` to disk as YAML.

**State serialization note:** `GuildState.role_emojis` uses tuple keys `(message_id, emoji_name)` which require custom transformation for YAML (converted to nested dicts `{msg_id: {emoji: state}}`).

## Key Conventions

- Uses `uvloop` as the async event loop
- Pydantic v2 API (`model_validate`, `.model_dump()`)
- Logging follows the pattern `logger.info("G=%r U=%r: ...", guild_name, username, ...)`
- Python version target: 3.13
- Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed)
- Ruff lint rules: isort (`I`) and pylint (`PL`) enabled in addition to defaults
