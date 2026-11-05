## Tickets Plugin

Lets users open private support ticket channels via `/adultier-adult`. Each ticket is a channel under a configurable category, visible only to the opener, bot, and support staff. Staff are pinged on open. Anyone in the channel can close the ticket (with confirmation) or add another user.

### User Commands

- **`/adultier-adult`** — Open a new ticket. Shows a modal to enter a topic. One open ticket per user at a time. Runs a pre-flight permission check before showing the modal.

### Button Channel Card

Declared as `BUTTON_ENTRY` in `__init__.py` (orange, 🆘 "Ask an Adultier Adult", custom_id `ticket_open` → `handle_ticket_open_button`). Shown only when the shared staff role (`GuildState.staff_role_id`, set with `/config common staff_role:`) is set — without a staff role there's nobody to ping. `_is_available` reads it from the core state passed in.

The button and the slash command share `_ticket_block_reason()` (required-role gate + duplicate-ticket guard) and `_topic_modal_rows()`, then both show the same topic modal. Both read only the cached member payload: a modal can't follow a deferred response, so both are bound by Discord's 3-second deadline.

**Staff opening on someone's behalf:** when the clicker holds the shared staff role (`gc.staff_role_id`) or owns the guild (`_is_staff`), the button shows an ephemeral user picker (`ticket_for_select` → `handle_ticket_for_select`) instead of the modal. Picking a member shows the topic modal with custom_id `ticket_topic_modal:{user_id}` (prefix-routed), with wording reminding staff the member will see the topic. Picking themselves opens an ordinary ticket. The ticket belongs to the picked member — channel name, `OpenTicket.user_id`, duplicate guard, and journal entry all use them. The required-role gate is skipped (reaching unvalidated members is a main use), but the one-ticket-per-member guard still applies. The welcome message @-mentions the member and names the staff opener; the log line and journal summary name the staff opener too. The `/adultier-adult` slash command has no on-behalf path. The picker handler and the modal handler both re-check `_is_staff` rather than trusting the target id in the custom_id; a non-staff submit falls back to opening a ticket for themselves.

The welcome message passes explicit `user_mentions`/`role_mentions` allow-lists: hikari suppresses pings by default, and the topic is free text that must not ping other roles.

### Configuration

Managed via `/config tickets` (admin only: MANAGE_GUILD or ADMINISTRATOR):

- **`set [category] [required_role]`** — Set any combination of config options. Omitted options are left unchanged.
- **`status`** — Show current configuration (including the shared staff role, which is set with `/config common`) and open ticket count.
- **`clear`** — Clear the category and required role (does not close open tickets).

State is persisted to `state/tickets_{guild_id}.yaml`.

### Ticket Lifecycle

1. User runs `/adultier-adult` or clicks the card → modal appears → submits topic (staff clicking the card pick a member first)
2. Bot creates `help-{username}` channel under configured category with `PRIVATE_CHANNEL_USER_PERMS` granted to the opener and the shared staff role
3. Bot posts staff ping (plus a mention of the member, if staff opened it for them) + topic + Close/Add Person buttons in channel
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
- `SEND_MESSAGES`, `VIEW_CHANNEL`, `READ_MESSAGE_HISTORY`, `ATTACH_FILES`, `MANAGE_MESSAGES` — granted to the bot in ticket channels; the user set (`PRIVATE_CHANNEL_USER_PERMS` in `context.py`) covers the opener, staff role, and anyone added via Add Person
