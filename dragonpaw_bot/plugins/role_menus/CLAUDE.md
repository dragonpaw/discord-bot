## Role Menus Plugin

Provides role selection using Discord's text select menus (dropdowns). Server admins configure role menus via TOML config files loaded through `/roles config`. Each menu appears as an embed with a dropdown in a designated channel.

### How It Works

1. Admin provides a TOML config URL via `/roles config`
2. Bot parses the flat TOML format directly into a `RolesConfig`
3. Bot deletes old messages in the role channel and posts new embeds with select menus
4. Members pick roles from dropdowns; the bot adds/removes roles accordingly
5. Single-select menus allow only one choice; multi-select menus allow picking many

### TOML Configuration

The role menu config is a standalone TOML file (not nested under `[roles]`):

```toml
channel = "role-select"

[[menu]]
name = "Colors"
description = "Pick your color"
single = false
options = [
  { role = "Red", emoji = "red_circle", description = "Red role" },
  { role = "Blue", description = "Blue role" },
]

[[menu]]
name = "DM Permission"
single = true
options = [
  { role = "DM: Open", description = "Feel free to DM me", emoji = "white_check_mark" },
  { role = "DM: Ask", description = "Please ask first", emoji = "question" },
]
```

### Select Menu Behavior

- **Single-select menus:** `max_values=1` — picking a new role replaces the old one
- **Multi-select menus:** `max_values=len(options)` — pick as many as you want
- Both types use `min_values=0` so members can deselect all
- Custom ID format: `role_menu:<menu_index>`
- Response: `DEFERRED_MESSAGE_CREATE` (ephemeral) followed by summary of changes via `edit_initial_response`

### State

Persisted as `state/role_menus_{guild_id}.yaml`, separate from the main guild state. Contains:

- `guild_id` / `guild_name` — Guild identifiers
- `role_channel_id` — The channel where menus are posted
- `role_names` — Dict mapping role ID to name (for logging)
- `menus` — List of `RoleMenuState` entries (menu_index, message_id, option_role_ids, single flag)

### File Structure

- **`__init__.py`** — Extension entry point (lightbulb Loader), `INTERACTION_HANDLERS` and `parse_role_config` exports
- **`commands.py`** — `parse_role_config()`, embed building, select menu building, `configure_role_menus()`, `handle_role_menu_interaction()`
- **`models.py`** — Pydantic models: config (`RoleMenuOptionConfig`, `RoleMenuConfig`, `RolesConfig`) and state (`RoleMenuState`, `RoleMenuGuildState`)
- **`state.py`** — YAML state persistence (load/save) with in-memory cache
- **`constants.py`** — `ROLE_MENU_PREFIX` interaction ID prefix

### Error Handling

- Missing roles: logged as errors, skipped in menu options, reported to guild log channel
- Missing emojis: logged as warnings (emoji is optional/decorative), option still included
- `ForbiddenError` on role add/remove: logged to guild log channel
- Menus with no valid options: posted as embed only (no select component)

### Logging

- **Info:** Menu creation, role changes (added/removed)
- **Debug:** Old message deletion, state loads
- **Warning:** Missing emojis, empty menus
- **Error:** Missing roles, invalid custom IDs

Log messages use `G=%r` for guild context and include `U=%r` where a user is involved.

### Limits

- Discord allows max 25 options per select menu (validated in `RoleMenuConfig`)
