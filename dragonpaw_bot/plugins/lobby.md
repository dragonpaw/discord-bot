## Lobby Plugin

Handles new member onboarding: auto-assigns a role, posts welcome messages, and optionally shows server rules with a click-through "I agree" button.

### Configuration (via TOML)

Configured through the `/config` command's `[lobby]` section:

- **channel** — The lobby/welcome channel name (required)
- **role** — A role to auto-assign on join and remove when rules are agreed to
- **welcome_message** — Message posted when a new member joins. Supports `{name}` (member mention) and `{days}` (kick deadline) substitutions.
- **rules** — Server rules text, posted as an embed in the lobby channel
- **click_for_rules** — If `true` (and a role is set), adds an "I agree" button to the rules embed. Clicking it removes the lobby role.
- **kick_after_days** — Number of days before inactive lobby members are kicked (stored in state, enforcement is external)

### Behavior

1. **Member joins** → auto-assigned the lobby role (if configured) → welcome message posted in lobby channel
2. **Rules embed** posted with optional "I agree" button
3. **Member clicks "I agree"** → lobby role removed, ephemeral confirmation sent
4. On `/config` reload, old bot messages in the lobby channel are deleted and rules are re-posted

### State

Stored in the main `GuildState` YAML:
- `lobby_channel_id`, `lobby_role_id`, `lobby_kick_days`
- `lobby_welcome_message`, `lobby_rules`, `lobby_click_for_rules`

### Required Discord Setup

- A lobby/welcome channel
- A lobby role (optional, needed for click-through rules)
- Bot needs Manage Roles permission to assign/remove the lobby role
