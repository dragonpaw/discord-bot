## Channel Cleanup Plugin

Auto-deletes messages older than a configured duration from any channel. Runs hourly as a background cron task with no user-facing commands (configuration is handled by the `config` plugin via `/config cleanup`).

### Configuration

Managed via `/config cleanup` (requires MANAGE_GUILD):

- **`add #channel expires:duration`** — Add a channel for auto-expiry.
- **`remove #channel`** — Stop monitoring a channel.
- **`status`** — Embed listing all configured channels with expiry info.

State is persisted to `state/channel_cleanup_{guild_id}.yaml`.

### Hourly Cleanup Cron

Runs at the top of each hour (`0 * * * *`). For each configured channel, calls `ChannelContext.purge_old_messages()` to delete messages older than the configured duration. Uses bulk delete (up to 100 messages per call) for messages younger than 14 days, and single deletes for older messages (Discord limitation). Per-guild error isolation — one guild's failure doesn't abort others.

### File Structure

- **`__init__.py`** — Extension entry point
- **`cron.py`** — Hourly cleanup cron task
- **`models.py`** — Pydantic models: `CleanupChannelEntry`, `CleanupGuildState`
- **`state.py`** — YAML state persistence (load/save with in-memory cache)

### Required Discord Permissions

- `MANAGE_MESSAGES` — to delete messages
- `READ_MESSAGE_HISTORY` — to fetch old messages

### Logging

- **Info**: Old messages purged (count logged)
- **Debug**: Cron tick, cleanup progress for large single-delete batches
- **Warning/Exception**: Per-guild cron errors
