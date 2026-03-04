## Birthdays Plugin

Tracks member birthdays and announces them in a configured channel. Users can self-register or admins can manage birthdays for others. Supports wishlists, a birthday role, and a week-ahead DM reminder.

### Per-Server Configuration

All settings are configurable per server via `/birthday config` (owner only). Settings are stored in `BirthdayGuildState` and persisted to YAML. Role fields default to None (guild-owner-only access) and channel fields default to None (disabled) until configured.

The guild owner (i.e. the Discord server owner, not a SubDay "owner") always passes role permission checks regardless of whether they have the required role.

Notifications (registrations, removals, config changes) are sent to the guild-wide log channel configured via `/logging`.

### Slash Commands (`/birthday`)

- **status** — Shows your registered birthday, wishlist URL, and days until your next birthday. Requires `register_role`. Ephemeral.
- **set [month] [day] [wishlist_url]** — Register or update your own birthday (month/day only, no year). Requires `register_role`. Optionally include a wishlist URL.
- **wishlist [url]** — Update just your wishlist URL. Requires `register_role`. With no argument, shows your current wishlist.
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

No birth year is collected or stored.

### Daily Cron Task

Runs once daily (e.g. 10:00 UTC). Per guild, with per-guild error isolation:

1. **Birthday announcements:** For each member whose birthday is today, post a themed embed in the announcement channel (if configured). The embed mentions the user and includes their wishlist link if set. Assign the birthday role (if configured).
2. **Birthday role cleanup:** Remove the birthday role from any member whose birthday was yesterday (i.e., their birthday is over).
3. **Week-ahead DM reminder:** For each member whose birthday is 7 days away, verify they are still in the guild (remove their entry and skip if not). DM them a reminder that their birthday is coming up. Show their current wishlist URL (if set) or note that none is set. Prompt them to review and update it before the day using `/birthday wishlist <url>`. Handle DM failures gracefully (log warning, don't crash).

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

- `config` — `BirthdayGuildConfig` with role/channel IDs
- `birthdays` — Dict mapping user ID to `BirthdayEntry`

### File Structure

- **`__init__.py`** — Extension entry point (lightbulb Loader), daily cron task, member leave listener
- **`commands.py`** — All slash commands, config interaction handlers, announcement embeds
- **`models.py`** — Pydantic models: `BirthdayEntry`, `BirthdayGuildConfig`, `BirthdayGuildState`
- **`state.py`** — YAML state persistence (load/save)
- **`constants.py`** — Configuration prefixes and constants

### Error Handling

- **DM failures:** Week-ahead reminder DMs catch `ForbiddenError` and log a warning. The cron continues for other users.
- **Guild log channel:** All notifications use `utils.log_to_guild()`, which silently skips if no log channel is configured.
- **Announcement channel:** Post failures are logged as warnings but don't crash the cron.
- **Birthday role:** Role assignment/removal failures are caught and logged.
- **Daily cron:** Per-guild error isolation — one guild's failure doesn't abort processing for other guilds.

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
