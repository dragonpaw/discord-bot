## Tickets Plugin

Lets users open private support ticket channels via `/adultier-adult`. Each ticket is a channel under a configurable category, visible only to the opener, bot, and support staff. Staff are pinged on open. Anyone in the channel can close the ticket (with confirmation) or add another user.

### User Commands

- **`/adultier-adult`** — Open a new ticket. Shows a modal to enter a topic. One open ticket per user at a time. Runs a pre-flight permission check before showing the modal.

### Button Channel Card

Declared as `BUTTON_ENTRY` in `__init__.py` (orange, 🆘 "Ask an Adultier Adult", custom_id `ticket_open` → `handle_ticket_open_button`). Shown only when `staff_role_id` is set — without a staff role there's nobody to ping.

The button and the slash command share `_ticket_block_reason()` (required-role gate + duplicate-ticket guard) and `_topic_modal_rows()`, then both show the same topic modal. Both read only the cached member payload: a modal can't follow a deferred response, so both are bound by Discord's 3-second deadline.

### Configuration

Managed via `/config tickets` (guild owner only):

- **`set [category] [staff_role] [required_role]`** — Set any combination of config options. Omitted options are left unchanged.
- **`status`** — Show current configuration and open ticket count.
- **`clear`** — Clear all configuration (does not close open tickets).

State is persisted to `state/tickets_{guild_id}.yaml`.

### Ticket Lifecycle

1. User runs `/adultier-adult` → modal appears → submits topic
2. Bot creates `help-{username}` channel under configured category with `PRIVATE_CHANNEL_USER_PERMS` granted to the opener and the configured staff role
3. Bot posts staff ping + topic + Close/Add Person buttons in channel
4. User receives ephemeral link to channel
5. Anyone in channel clicks **Close Ticket 🔒** → confirmation → channel deleted, state cleaned up
6. Anyone in channel clicks **Add Person 👤** → user select → selected user granted `PRIVATE_CHANNEL_USER_PERMS` (including `ATTACH_FILES`)

### File Structure

- **`__init__.py`** — `INTERACTION_HANDLERS`, `MODAL_HANDLERS`, and `BUTTON_ENTRY` exports (no loader here — `load_extensions_from_package` doesn't import `__init__.py`)
- **`commands.py`** — `lightbulb.Loader()`, `/adultier-adult` command (`AdultierAdultCommand`), modal handler, all button/select handlers
- **`config.py`** — `/config tickets` subcommands
- **`models.py`** — `OpenTicket`, `TicketGuildState` pydantic models
- **`state.py`** — YAML state persistence (load/save with in-memory cache)

### Required Discord Permissions

- `MANAGE_CHANNELS` — create and delete ticket channels, set permission overwrites
- `SEND_MESSAGES`, `VIEW_CHANNEL`, `READ_MESSAGE_HISTORY`, `ATTACH_FILES` — for the bot, opener, staff role, and any user added via Add Person inside ticket channels (see `PRIVATE_CHANNEL_USER_PERMS` in `context.py`)
