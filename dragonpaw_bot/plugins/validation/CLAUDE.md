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

### Rejection

There is no reject button. Staff close the validate channel manually (deleting the channel
removes the member from Discord's view; bot state is cleaned up on the next
`MemberUpdateEvent` or left for garbage collection — no orphan tracking is needed since
the channel deletion is the source of truth for staff).

### Configuration

Managed via `/config validation` (owner only):

- **`setup [lobby_channel] [validate_category] [announce_channel] [member_role] [staff_role] [max_reminders]`**
  — Set any combination. Omitted params keep current values.
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
- **`commands.py`** — All event listeners, cron task, interaction/modal handlers
- **`config.py`** — `/config validation` subcommands
- **`models.py`** — `ValidationStage`, `ValidationMember`, `ValidationGuildState`
- **`state.py`** — YAML state persistence (load/save with in-memory cache)
- **`assets/`** — Sample verification images

### Required Discord Permissions

- `MANAGE_CHANNELS` — create and delete validate channels, set permission overwrites
- `MANAGE_ROLES` — assign the member role on approval
- `MANAGE_NICKNAMES` — set approved name as server nickname
- `KICK_MEMBERS` — auto-kick after max reminders
- `SEND_MESSAGES`, `VIEW_CHANNEL` — post in lobby and validate channels
