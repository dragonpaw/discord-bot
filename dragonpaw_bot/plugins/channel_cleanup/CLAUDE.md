## Channel Cleanup Plugin

Auto-deletes messages **and threads** older than a configured duration from any channel. Runs hourly as a background cron task with no user-facing commands (configuration is handled by the `config` plugin via `/config cleanup`).

### Configuration

Managed via `/config cleanup` (guild owner only):

- **`add #channel expires:duration`** — Add a channel for auto-expiry.
- **`remove #channel`** — Stop monitoring a channel.
- **`status`** — Embed listing all configured channels with expiry info.

State is persisted to `state/channel_cleanup_{guild_id}.yaml`.

### Hourly Cleanup Cron

Runs at the top of each hour (`0 * * * *`). For each configured channel, first checks bot permissions via `cc.check_perms(CHANNEL_CLEANUP_PERMS)` — if any are missing, logs a warning and posts to the guild log channel, then skips that channel. Otherwise:

1. Calls `ChannelContext.purge_old_messages()` to delete messages older than the configured duration.
2. Calls `ChannelContext.purge_old_threads()` to delete threads whose last activity (last message, or creation time if no messages) is older than the configured duration.

`purge_old_messages` uses bulk delete (up to 100 messages per call) for messages younger than 14 days, and single deletes for older messages (Discord limitation). `purge_old_threads` fetches both active threads (guild-wide, filtered by parent channel) and archived public threads, then deletes stale ones via `rest.delete_channel`.

Per-guild error isolation — one guild's failure doesn't abort others. Message and thread cleanup are also isolated from each other — a thread error doesn't abort message cleanup.

Note: `fetch_messages` silently returns empty results (rather than raising) when the bot lacks `READ_MESSAGE_HISTORY`, so the permission check must be proactive rather than reactive.

### File Structure

- **`__init__.py`** — Extension entry point
- **`cron.py`** — Hourly cleanup cron task
- **`models.py`** — Pydantic models: `CleanupChannelEntry`, `CleanupGuildState`
- **`state.py`** — YAML state persistence (load/save with in-memory cache)

### Required Discord Permissions

- `VIEW_CHANNEL` — to see the channel
- `MANAGE_MESSAGES` — to delete messages
- `READ_MESSAGE_HISTORY` — to fetch old messages
- `MANAGE_THREADS` — to delete threads

### Logging

- **Info**: Old messages purged (count logged); old threads purged (count logged)
- **Debug**: Cron tick, cleanup progress for large single-delete batches
- **Warning**: Missing channel permissions detected at cron time — structlog warning + guild log message with instructions to fix; thread fetch/delete failures
- **Warning/Exception**: Unexpected cron errors — structlog exception + guild log message
