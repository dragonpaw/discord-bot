## Role Menus Plugin

Posts embed menus with emoji reactions in a designated channel. Members click reactions to self-assign or remove Discord roles.

### Configuration (via TOML)

Configured through the `/config` command's `[roles]` section:

- **channel** — The channel where role menus are posted (required)
- **menu** — A list of menu definitions, each with:
  - **name** — Menu title
  - **description** — Optional description text
  - **single** — If `true`, picking one option removes all others (single-select mode). Adds "(Pick 1)" to the title.
  - **options** — List of role options, each with:
    - **role** — The Discord role name to assign
    - **emoji** — The custom emoji name used as the reaction
    - **description** — Description shown in the embed field

### Behavior

1. On `/config` reload, all old bot messages in the role channel are deleted
2. One embed per menu is posted, with emoji reactions added by the bot
3. **Reaction added** → role assigned to the member. For single-select menus, all other roles from that menu are removed.
4. **Reaction removed** → role removed from the member
5. Embeds use rainbow colors cycling across menus
6. A usage note is posted after all menus explaining how reactions work

### Single-Select Menus

When `single = true`, choosing a new option automatically removes all other roles from that menu. The embed includes a note explaining this behavior.

### Error Handling

- Missing emojis or roles are logged and reported via `utils.report_errors`
- `ForbiddenError` on role add/remove is caught and reported to the guild
- Menus with no valid options are posted but with a warning logged

### State

Stored in the main `GuildState` YAML:
- `role_channel_id` — The channel ID where menus are posted
- `role_emojis` — Maps `(message_id, emoji_name)` tuples to `RoleMenuOptionState` (which role to add and which to remove)

### Required Discord Setup

- A role channel for the menus
- Custom emoji for each menu option
- Roles matching the names in the config
- Bot needs Manage Roles permission (and its role must be above the assigned roles in the hierarchy)
