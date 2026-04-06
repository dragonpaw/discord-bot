# Channel Cleanup — Thread Auto-Deletion

**Date:** 2026-04-05

## Summary

Extend the channel_cleanup plugin so that threads (active and archived) inside a configured cleanup channel are automatically deleted when they have had no activity within the configured expiry duration. No new config options — thread cleanup is always-on for every configured cleanup channel.

## Changes

### `context.py`

**`CHANNEL_CLEANUP_PERMS`** — Add `MANAGE_THREADS: "Manage Threads"`. Required to delete threads (which are channels in Discord's model).

**New method `ChannelContext.purge_old_threads(expiry_minutes) -> int`:**

1. Compute `cutoff = now - timedelta(minutes=expiry_minutes)`.
2. Collect threads to delete from two sources:
   - **Active threads:** `rest.fetch_active_threads(guild_id)` — filter to threads where `thread.parent_id == self.channel_id`.
   - **Archived public threads:** `rest.fetch_public_archived_threads(self.channel_id)`.
3. For each thread, determine last activity:
   - If `thread.last_message_id` is set, extract its timestamp via hikari's snowflake utilities.
   - Otherwise fall back to the thread's own creation timestamp.
4. If last activity < cutoff, delete via `rest.delete_channel(thread_id)`.
5. Return count of deleted threads.
6. Errors: catch `ForbiddenError` / `NotFoundError` per thread — log warning + guild log message on `ForbiddenError`, skip `NotFoundError` (already gone).

**`ChannelContext.run_cleanup(expiry_minutes)`** — After `purge_old_messages`, call `purge_old_threads(expiry_minutes)`. Thread errors are isolated so message cleanup already completed is not undone.

### `plugins/channel_cleanup/CLAUDE.md`

Document that thread cleanup runs automatically alongside message cleanup, including the `MANAGE_THREADS` permission requirement.

## Permissions

| Permission | Purpose |
|---|---|
| `VIEW_CHANNEL` | existing |
| `READ_MESSAGE_HISTORY` | existing |
| `MANAGE_MESSAGES` | existing |
| `MANAGE_THREADS` | **new** — delete threads |

## Non-Changes

- No new config fields or commands.
- No changes to `models.py`, `state.py`, `cron.py`, or `config.py`.
- `/config cleanup status` output is unchanged (thread cleanup is implicit).
