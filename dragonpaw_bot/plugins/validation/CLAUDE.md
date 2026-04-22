## Validation Plugin

Handles the full member onboarding and age-verification flow. Replaces the old lobby plugin.
New members are greeted in a lobby channel, prompted to read the rules, then guided through
a private verify channel where they submit age-verification photos for staff review.

### Flow

1. **Member joins** — bot posts a welcome message in the lobby channel with an "I've read
   the rules! ✅" button (custom ID: `validation_rules_agreed:{user_id}`). Member is added
   to state with `stage=AWAITING_RULES`.

2. **Rules agreed** — only the tagged member can click their button. Bot creates a private
   `#validate-{name}` channel (staff role added immediately for visibility, no ping yet),
   posts photo instructions with sample images, updates stage to `AWAITING_PHOTOS`.

3. **Photos submitted** — `GuildMessageCreateEvent` listener counts image attachments from
   the member. When `photo_count >= 2`, stage moves to `AWAITING_STAFF`, bot pings staff
   in the validate channel with a "Looks good! ✅" button.

4. **Staff approves** — clicking the button shows a modal to enter the approved name. On
   submit: bot sets the member's server nickname, assigns the member role, posts an
   announcement in the general channel, closes the validate channel, and removes the
   member from state.

5. **Reminders / auto-kick** — hourly cron checks members stuck at `AWAITING_RULES`. Every
   24 hours a lobby reminder is posted. After `max_reminders` reminders the member is kicked.

### Member Leave Cleanup

If a member leaves the server mid-onboarding, `on_member_leave` (`MemberDeleteEvent`)
removes them from state and fires `_close_validate_channel` with a 30-second delay so
staff see a goodbye notice before the channel disappears. `event.old_member` may be
`None` (cache miss) — user ID is always used for the Discord mention.

### Startup Reconciliation

`on_startup_reconcile` (`StartedEvent`) iterates all guilds with persisted state via
`all_guild_ids()` and calls `_reconcile_guild()` for each. For every member entry:

- **Member left while offline** — REST 404 on `fetch_member` → remove from state, fire
  `_close_validate_channel` (30-second delay) if a channel exists.
- **Channel deleted while offline** — member present but REST 404 on `fetch_channel` →
  remove from state (no channel to delete).
- HTTP errors other than 404 → log a warning and skip that entry.

Each guild is wrapped in its own `try/except` so one failure doesn't abort others.

### Rejection

There is no reject button. Staff close the validate channel manually (deleting the channel
removes the member from Discord's view; bot state is cleaned up on the next
`MemberDeleteEvent` or `StartedEvent` reconcile).

### Configuration

Managed via `/config validation` (owner only):

- **`setup [lobby_channel] [validate_category] [member_role] [staff_role] [max_reminders]`**
  — Set any combination. Omitted params keep current values. The welcome announcement channel is configured globally via `/config channels general`.
- **`status`** — Shows config + member counts at each stage.

State persisted to `state/validation_{guild_id}.yaml`.

### State

`ValidationGuildState` holds both config fields and the runtime `members` list.
Each `ValidationMember` tracks: `user_id`, `joined_at`, `reminder_count`,
`stage` (`ValidationStage` enum), `channel_id`, `photo_count`.

### Assets

Sample images live in `assets/` and are attached via `hikari.File`:
- `validation-id.jpg` — example government ID photo
- `validation-selfie.jpg` — example selfie holding ID (**must be added before going live**)

### Interaction custom IDs

- `validation_rules_agreed:{user_id}` — rules button (component)
- `validation_approve:{channel_id}` — staff approve button (component)
- `validation_approve_modal:{channel_id}` — name-entry modal (modal)

### File Structure

- **`__init__.py`** — Loader re-export, `INTERACTION_HANDLERS`, `MODAL_HANDLERS`
- **`commands.py`** — All event listeners (`on_member_join`, `on_member_leave`,
  `on_startup_reconcile`, `on_member_update`, `on_message_create`), cron task,
  interaction/modal handlers, and helpers (`_close_validate_channel`, `_reconcile_guild`,
  `_sanitize_channel_name`, `_is_staff`)
- **`config.py`** — `/config validation` subcommands
- **`models.py`** — `ValidationStage`, `ValidationMember`, `ValidationGuildState`
- **`state.py`** — YAML state persistence (load/save with in-memory cache).
  `all_guild_ids()` returns all guild IDs with persisted state files on disk.
- **`assets/`** — Sample verification images

### Security Invariants

These properties are enforced in code and must be preserved:

- **Button ownership** — The rules-agreed button embeds the member's user ID in its custom ID (`validation_rules_agreed:{user_id}`). `handle_rules_agreed` rejects any clicker whose ID doesn't match. No other user can advance someone else through onboarding.
- **Self-approval prevention** — Both `handle_approve_button` and `handle_approve_modal` reject the interaction if the clicker's user ID matches the `user_id` on the state entry for that channel.
- **Staff-only approval** — `_is_staff` requires either ADMINISTRATOR permission or the configured staff role. Non-staff get an ephemeral rejection before any modal is shown.
- **Photo counting isolation** — `on_message_create` only counts photos when all three conditions are true: the message is in the member's assigned validate channel, the author is that specific member, and the member's stage is `AWAITING_PHOTOS`. Photos posted to other channels, by other users, or after the stage has advanced are silently ignored.
- **Nickname strip** — The approved name is `.strip()`-ed before the empty check, preventing whitespace-only nicknames from passing `if not name`.
- **Channel name safety** — `_sanitize_channel_name` falls back to `"validate-member"` if the display name produces an empty string after stripping non-alphanumeric characters (e.g. emoji-only display names).

**Known limitation — double-approval race:** Two staff members clicking approve simultaneously can both pass all checks (shared in-memory state cache, no locking). Both runs will set the nickname and role (idempotent on Discord's side) and attempt to delete the validate channel (second attempt silently swallowed by `delete_channel`'s `NotFoundError` handler), but the general-channel welcome announcement will fire twice. This is an accepted risk given the low probability and the operational complexity of adding a distributed lock.

### Required Discord Permissions

- `MANAGE_CHANNELS` — create and delete validate channels, set permission overwrites
- `MANAGE_ROLES` — assign the member role on approval
- `MANAGE_NICKNAMES` — set approved name as server nickname
- `KICK_MEMBERS` — auto-kick after max reminders
- `SEND_MESSAGES`, `VIEW_CHANNEL` — post in lobby and validate channels
