## Role Menus Plugin

Provides role selection using Discord's text select menus (dropdowns). Server admins configure role menus via TOML config files loaded through `/config`. Each menu appears as an embed with a dropdown in a designated channel.

### How It Works

1. Admin provides a TOML config via `/config` with a `[roles]` section
2. Bot deletes old messages in the role channel and posts new embeds with select menus
3. Members pick roles from dropdowns; the bot adds/removes roles accordingly
4. Single-select menus allow only one choice; multi-select menus allow picking many

### TOML Configuration

```toml
[roles]
channel = "role-select"

[[roles.menu]]
name = "Colors"
description = "Pick your color"
single = false

[[roles.menu.options]]
role = "Red"
emoji = "red_circle"    # Optional — decorative on dropdown
description = "Red role"
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

- **`__init__.py`** — Extension entry point (lightbulb Loader), `INTERACTION_HANDLERS` export
- **`commands.py`** — Embed building, select menu building, `configure_role_menus()`, `handle_role_menu_interaction()`
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
