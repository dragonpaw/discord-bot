## Birthdays Plugin

Tracks member birthdays and announces them in a configured channel. Users can self-register or admins can manage birthdays for others. Supports wishlists, a birthday role, and a week-ahead DM reminder.

### Per-Server Configuration

All settings are configurable per server via `/config birthday settings` (owner only). Settings are stored in `BirthdayGuildState` and persisted to YAML. Role fields default to None (guild-owner-only access) and channel fields default to None (disabled) until configured.

The guild owner (i.e. the Discord server owner, not a SubDay "owner") always passes role permission checks regardless of whether they have the required role.

Notifications (registrations, removals, config changes) are sent to the guild-wide log channel configured via `/config bot logging`.

### Slash Commands (`/birthday`)

- **status** — Shows your registered birthday, wishlist URL, and days until your next birthday. Requires `register_role`. Ephemeral.
- **set** — Register or update your own birthday via a multi-step interactive flow: month (select menu) → day + wishlist URL (text input modal) → region (select menu) → timezone (select menu). No year collected. Requires `register_role`. Wishlist URL is pre-filled from existing entry when updating.
- **wishlist [url]** — View or update your wishlist URL. Requires `register_role`. With no argument, shows your current wishlist.
- **remove** — Remove your own birthday entry. Requires `register_role`.
- **remove-for @user** — Requires `manage_role`. Remove another user's birthday entry.
- **list** — Available to all server members (no role required). Shows all registered birthdays in a single embed grouped by month with emoji headers, sorted by day. Includes wishlist links where set.


### Config Commands (`/config birthday`)

- **settings** — Owner only. Shows current settings with interactive select menus. Each menu displays the current configured value (not None/blank) as its default selection. Changes save immediately on each selection.

### Config Settings (`/config birthday settings`)

- **Register role(s)** (role multi-select, default: _None/owner-only_) — Role(s) allowed to self-register, view status, update wishlist, and remove own birthday
- **Manage role** (role select, default: _None/owner-only_) — Role allowed to set/remove birthdays for others
- **List role** (role select, default: _None/owner-only_) — Role allowed to list all birthdays
- **Announcement channel** (channel select, default: _None/disabled_) — Channel where birthday announcements are posted
- **Birthday role** (role select, default: _None/disabled_) — Role auto-assigned on the user's birthday and removed the next day

### Birthday Entry

Each entry stores:

- `user_id` — Discord user ID
- `month` — Birth month (1–12)
- `day` — Birth day (1–31)
- `wishlist_url` — Optional wishlist link (string or None)
- `timezone` — Optional IANA timezone string (e.g. `America/New_York`), defaults to UTC
- `last_announced` — Date of last public announcement (prevents double-posting)

No birth year is collected or stored.

### Hourly Cron Task

Runs every hour (`0 * * * *`). Per guild, with per-guild error isolation. For each user, computes the current date and hour in their configured timezone (defaulting to UTC). Only processes events when the user's local hour is 0 (midnight):

1. **Birthday announcements:** If it's the user's birthday in their local timezone and `last_announced` doesn't match today, post a themed embed in the announcement channel (if configured). Assign the birthday role (if configured). Update `last_announced` to prevent double-posting.
2. **Birthday role cleanup:** If the user's birthday was yesterday in their local timezone and a birthday role is configured, remove the role.
3. **Week-ahead DM reminder:** If the user's birthday is 7 days away in their local timezone, verify they are still in the guild (remove their entry and skip if not). DM them a reminder with their current wishlist URL. Handle DM failures gracefully.

### Member Leave Cleanup

Listen for `hikari.MemberDeleteEvent`. When a member leaves a guild, automatically remove their birthday entry from that guild's state. Log the removal.

### Announcement Embed

Posted in the configured announcement channel on the member's birthday:

- Themed embed (festive color)
- Mentions the birthday user
- Shows wishlist link as a clickable hyperlink if set
- Unique leading emoji for guild log messages (e.g. 🎂)

### State

Persisted as `state/birthdays_{guild_id}.yaml`, separate from the main guild state. Contains:

- `config` — `BirthdayGuildConfig` with role/channel names
- `birthdays` — Dict mapping user ID to `BirthdayEntry`

### File Structure

- **`__init__.py`** — Extension entry point (lightbulb Loader), interaction/modal handler exports
- **`listeners.py`** — Member leave cleanup listener
- **`cron.py`** — Hourly cron task, birthday announcement/role/reminder helpers
- **`commands.py`** — Non-config slash commands, announcement embeds
- **`config.py`** — Config command (`/config birthday settings`), config interaction handler, config UI helpers
- **`models.py`** — Pydantic models: `BirthdayEntry`, `BirthdayGuildConfig`, `BirthdayGuildState`
- **`state.py`** — YAML state persistence (load/save)
- **`constants.py`** — Interaction ID prefixes and timezone region data

### Error Handling

- **DM failures:** Week-ahead reminder DMs catch `ForbiddenError` and log a warning. The cron continues for other users.
- **Guild log channel:** All notifications use `gc.log()` (on `GuildContext`), which silently skips if no log channel is configured.
- **Announcement channel:** Post failures are logged as warnings but don't crash the cron.
- **Birthday role:** Role assignment/removal failures are caught and logged.
- **Hourly cron:** Per-guild error isolation — one guild's failure doesn't abort processing for other guilds.

### Logging

- **Info:** Birthday registrations, updates, removals, config changes, announcements posted, role assigned/removed
- **Debug:** State loads, DM delivery, cron tick timing
- **Warning:** DM failures, missing channels/roles, member leave cleanup

All log messages use structlog with `guild=` and `user=` keyword arguments. Interaction handlers rely on contextvars from the central dispatcher.

### Required Discord Setup

- Announcement channel via `/config birthday settings`
- Birthday role via `/config birthday settings` (optional)
- Permission roles via `/config birthday settings` (optional, defaults to owner-only)
- Guild-wide log channel via `/config bot logging` (optional)
- Bot needs Manage Roles permission if birthday role is configured
