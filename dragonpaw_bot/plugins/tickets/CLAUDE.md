## Tickets Plugin

Lets users open private support ticket channels via `/help`. Each ticket is a channel under a configurable category, visible only to the opener, bot, and support staff. Staff are pinged on open. Anyone in the channel can close the ticket (with confirmation) or add another user.

### User Commands

- **`/ticket`** — Open a new ticket. Shows a modal to enter a topic. One open ticket per user at a time.

### Configuration

Managed via `/config tickets` (requires MANAGE_GUILD):

- **`set [category] [staff_role] [required_role]`** — Set any combination of config options. Omitted options are left unchanged.
- **`status`** — Show current configuration and open ticket count.
- **`clear`** — Clear all configuration (does not close open tickets).

State is persisted to `state/tickets_{guild_id}.yaml`.

### Ticket Lifecycle

1. User runs `/ticket` → modal appears → submits topic
2. Bot creates `help-{username}` channel under configured category
3. Bot posts staff ping + topic + Close/Add Person buttons in channel
4. User receives ephemeral link to channel
5. Anyone in channel clicks **Close Ticket 🔒** → confirmation → channel deleted, state cleaned up
6. Anyone in channel clicks **Add Person 👤** → user select → selected user granted access

### File Structure

- **`__init__.py`** — Loader, `INTERACTION_HANDLERS`, `MODAL_HANDLERS` exports
- **`commands.py`** — `/help` command, modal handler, all button/select handlers
- **`config.py`** — `/config tickets` subcommands
- **`models.py`** — `OpenTicket`, `TicketGuildState` pydantic models
- **`state.py`** — YAML state persistence (load/save with in-memory cache)

### Required Discord Permissions

- `MANAGE_CHANNELS` — create and delete ticket channels, set permission overwrites
- `SEND_MESSAGES`, `VIEW_CHANNEL` — post in ticket channels
