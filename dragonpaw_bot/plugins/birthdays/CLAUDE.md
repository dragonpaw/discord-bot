## Birthdays Plugin

Tracks member birthdays and announces them in a configured channel. Users can self-register or admins can manage birthdays for others. Supports wishlists, a birthday role, and a week-ahead DM reminder.

### Per-Server Configuration

All settings are configurable per server via `/birthday config` (owner only). Settings are stored in `BirthdayGuildState` and persisted to YAML. Role fields default to None (guild-owner-only access) and channel fields default to None (disabled) until configured.

The guild owner (i.e. the Discord server owner, not a SubDay "owner") always passes role permission checks regardless of whether they have the required role.

Notifications (registrations, removals, config changes) are sent to the guild-wide log channel configured via `/logging`.

### Slash Commands (`/birthday`)

- **status** — Shows your registered birthday, wishlist URL, and days until your next birthday. Requires `register_role`. Ephemeral.
- **set** — Register or update your own birthday via a 3-step interactive select menu flow: month → day → region → timezone. No year collected. Requires `register_role`. Preserves existing wishlist URL on update.
- **wishlist [url]** — View or update your wishlist URL. Requires `register_role`. With no argument, shows your current wishlist.
- **set-for @user [month] [day] [wishlist_url]** — Requires `manage_role`. Register or update a birthday for another user.
- **remove** — Remove your own birthday entry. Requires `register_role`.
- **remove-for @user** — Requires `manage_role`. Remove another user's birthday entry.
- **list** — Requires `list_role`. Shows all registered birthdays grouped by month, sorted by day. Includes wishlist links where set.
- **config** — Owner only. Shows current settings with interactive select menus. Each menu displays the current configured value (not None/blank) as its default selection. Changes save immediately on each selection.

### Config Settings (`/birthday config`)

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

- **`__init__.py`** — Extension entry point (lightbulb Loader), hourly cron task, member leave listener
- **`commands.py`** — All slash commands, config interaction handlers, announcement embeds
- **`models.py`** — Pydantic models: `BirthdayEntry`, `BirthdayGuildConfig`, `BirthdayGuildState`
- **`state.py`** — YAML state persistence (load/save)
- **`constants.py`** — Interaction ID prefixes and timezone region data

### Error Handling

- **DM failures:** Week-ahead reminder DMs catch `ForbiddenError` and log a warning. The cron continues for other users.
- **Guild log channel:** All notifications use `utils.log_to_guild()`, which silently skips if no log channel is configured.
- **Announcement channel:** Post failures are logged as warnings but don't crash the cron.
- **Birthday role:** Role assignment/removal failures are caught and logged.
- **Hourly cron:** Per-guild error isolation — one guild's failure doesn't abort processing for other guilds.

### Logging

- **Info:** Birthday registrations, updates, removals, config changes, announcements posted, role assigned/removed
- **Debug:** State loads, DM delivery, cron tick timing
- **Warning:** DM failures, missing channels/roles, member leave cleanup

All log messages follow the pattern `logger.info("G=%r U=%r: ...", guild_name, username, ...)`.

### Required Discord Setup

- Announcement channel via `/birthday config`
- Birthday role via `/birthday config` (optional)
- Permission roles via `/birthday config` (optional, defaults to owner-only)
- Guild-wide log channel via `/logging` (optional)
- Bot needs Manage Roles permission if birthday role is configured
