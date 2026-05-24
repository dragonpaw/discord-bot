## Intros Plugin

Manages a server introductions channel where each member is expected to post one introduction message. Runs a daily cron to remove stale intro posts from members who have left the guild, and provides a `/intros missing` command to identify members who haven't posted yet.

### Configuration

Managed via `/config intros` (guild owner only):

- **`set #channel [role:role] [missing_role:role]`** — Set the introductions channel. The optional `role` restricts the `/intros missing` / weekly scan to only members who have that role (e.g. a "verified member" role). The optional `missing_role` is added by the bot to anyone subject to the check who hasn't posted, and removed once they have. No other role exempts a member from needing an intro — eligibility is decided purely by `required_role_id`. Warns if the bot lacks required permissions or can't manage `missing_role`, but saves anyway.
- **`clear`** — Remove the intros configuration for this guild (channel, required role, missing-intro role).

State is persisted to `state/intros_{guild_id}.yaml`.

### Slash Commands (`/intros`)

- **`missing`** — Lists members (filtered by required role if configured) who have not posted in the intros channel. Requires MANAGE_GUILD. Responds with @mentions of missing members, or a success message if everyone has posted. If `missing_role_id` is configured, also adds the role to missing members and removes it from members who have now posted; the response and log message report what was changed.

### Cron Tasks

#### Daily Cleanup Cron

Runs at 9am UTC daily (`0 9 * * *`). For each configured guild:

1. Checks bot permissions (`READ_MESSAGE_HISTORY`, `MANAGE_MESSAGES`) in the intros channel — logs a warning to the guild log channel and skips if missing.
2. Fetches pinned message IDs — pinned messages are never touched.
3. Builds the current set of guild member IDs.
4. Iterates all messages in the intros channel (newest-first); skips pinned; deletes any from authors no longer in the guild; deletes older duplicate posts (keeping only the newest per author).
5. If any were deleted, posts separate cute log messages for departed-member removals and duplicate removals.

#### Weekly Naughty List Cron

Runs at 8pm UTC Saturday (`0 20 * * 6` = noon PST / 1pm PDT). For each configured guild:

1. Checks that `channel_id` is configured — skips if not.
2. Reads `GuildState.general_channel_id` via `bot.state(guild_id)` — skips if not set.
3. Same filter logic as `/intros missing`: fetches all messages (skipping pinned and bots), collects poster IDs, finds members (with required role if set) who haven't posted.
4. If `missing_role_id` is configured, syncs that role across eligible members: adds it to anyone missing without it, removes it from anyone who has it but has now posted.
5. If nobody missing: posts an all-clear celebration message to the general channel (mentioning role removals if any).
6. If members missing: posts @mentions with a "naughty list" message to the general channel and notes the role additions/removals.
7. Logs summary to `gc.log()` including the role add/remove counts.

### File Structure

- **`__init__.py`** — Extension entry point (lightbulb Loader), registers `/intros` command group
- **`cron.py`** — Daily cleanup cron task; weekly naughty list cron task; shared `scan_intros()` helper that finds missing members and syncs the `missing_role` if configured
- **`commands.py`** — `/intros missing` slash command (uses `scan_intros()`)
- **`config.py`** — `/config intros set|clear` command registration
- **`models.py`** — Pydantic model: `IntrosGuildState`
- **`state.py`** — YAML state persistence (load/save with in-memory cache)

### Required Discord Permissions

- `READ_MESSAGE_HISTORY` — to iterate messages in the intros channel
- `MANAGE_MESSAGES` — to delete departed members' intro posts
- `MANAGE_ROLES` (+ role hierarchy) — only if `missing_role_id` is configured; needed to add/remove the missing-intro role

### Logging

- **Info:** Config changes, `/intros missing` results, individual intro deletions, weekly naughty list results
- **Debug:** Cron tick, per-guild scan start
- **Warning:** Missing channel permissions (cron), permission errors on delete

Log message emojis: `📋` config, `🧹` daily removal, `⚠️` warnings, `👀` missing command result.
