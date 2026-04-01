## Intros Plugin

Manages a server introductions channel where each member is expected to post one introduction message. Runs a daily cron to remove stale intro posts from members who have left the guild, and provides a `/intros missing` command to identify members who haven't posted yet.

### Configuration

Managed via `/config intros` (guild owner only):

- **`set #channel [role:role]`** — Set the introductions channel. The optional `role` restricts the `/intros missing` check to only members who have that role (e.g. a "verified member" role). Warns if the bot lacks required permissions but saves anyway.
- **`clear`** — Remove the intros configuration for this guild.

State is persisted to `state/intros_{guild_id}.yaml`.

### Slash Commands (`/intros`)

- **`missing`** — Lists members (filtered by required role if configured) who have not posted in the intros channel. Requires MANAGE_GUILD. Responds with @mentions of missing members, or a success message if everyone has posted. Also logs a summary to the guild log channel.

### Cron Tasks

#### Daily Cleanup Cron

Runs at 9am UTC daily (`0 9 * * *`). For each configured guild:

1. Checks bot permissions (`READ_MESSAGE_HISTORY`, `MANAGE_MESSAGES`) in the intros channel — logs a warning to the guild log channel and skips if missing.
2. Builds the current set of guild member IDs.
3. Iterates all messages in the intros channel; deletes any from authors no longer in the guild.
4. If any were deleted, posts a log message naming the removed users.

#### Weekly Naughty List Cron

Runs at 8pm UTC Saturday (`0 20 * * 6` = noon PST / 1pm PDT). For each configured guild:

1. Checks that `channel_id` is configured — skips if not.
2. Reads `GuildState.general_channel_id` via `bot.state(guild_id)` — skips if not set.
3. Same filter logic as `/intros missing`: fetches all messages, collects poster IDs, finds members (with required role if set) who haven't posted.
4. If nobody missing: posts an all-clear celebration message to the general channel.
5. If members missing: posts @mentions with a "naughty list" message to the general channel.
6. Logs summary to `gc.log()`.

### File Structure

- **`__init__.py`** — Extension entry point (lightbulb Loader), registers `/intros` command group
- **`cron.py`** — Daily cleanup cron task; weekly naughty list cron task
- **`commands.py`** — `/intros missing` slash command
- **`config.py`** — `/config intros set|clear` command registration
- **`models.py`** — Pydantic model: `IntrosGuildState`
- **`state.py`** — YAML state persistence (load/save with in-memory cache)

### Required Discord Permissions

- `READ_MESSAGE_HISTORY` — to iterate messages in the intros channel
- `MANAGE_MESSAGES` — to delete departed members' intro posts

### Logging

- **Info:** Config changes, `/intros missing` results, individual intro deletions, weekly naughty list results
- **Debug:** Cron tick, per-guild scan start
- **Warning:** Missing channel permissions (cron), permission errors on delete

Log message emojis: `📋` config, `🧹` daily removal, `⚠️` warnings, `👀` missing command result.
