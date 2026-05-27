## Intros Plugin

Manages a server introductions channel where each member is expected to post one introduction message. The missing-intro role is driven by three events: it's **removed live** the moment a flagged member posts, **added daily** to members who still haven't posted, and the holders are **publicly shamed weekly**. Also runs a daily cron to remove stale intro posts from members who have left the guild, and provides a `/intros missing` command to identify members who haven't posted yet.

### Missing-intro role lifecycle

The `missing_role` (if configured) is managed by these paths:

- **Seeded on validation** — when the validation plugin approves a new member, it assigns this role (if the intros channel + `missing_role` are both configured) so the newcomer stays intro-gated until they post. See `plugins/validation/CLAUDE.md`.
- **Removed live** — a `GuildMessageCreateEvent` listener removes the role the instant a member who has it posts in the intros channel. The listener does *not* check `required_role_id`, so posting always clears the role regardless of eligibility.
- **Reconciled daily** — the daily cron's `scan_intros()` is a two-way sync over **eligible members only**: it adds the role to eligible members who still haven't posted, **and** strips it from eligible holders who have since posted. This is the safety net that recovers stale roles when a live removal never fired (e.g. the bot was offline when the member posted).
- **Shamed weekly** — the weekly cron re-runs the same reconciliation, then posts the role-wearers publicly in the general channel.

**Eligibility scope:** `scan_intros()` only adds/removes the role for members passing the `required_role_id` filter. A holder who is *not* eligible (e.g. a validation-seeded member who doesn't yet have the required role) is left untouched by reconciliation — their role only clears when they post. This keeps the validation-seeded gate intact regardless of how `required_role_id` is configured.

### Configuration

Managed via `/config intros` (guild owner only):

- **`set #channel [role:role] [missing_role:role]`** — Set the introductions channel. The optional `role` restricts the `/intros missing` / weekly scan to only members who have that role (e.g. a "verified member" role). The optional `missing_role` is added by the bot to anyone subject to the check who hasn't posted, and removed once they have. No other role exempts a member from needing an intro — eligibility is decided purely by `required_role_id`. Warns if the bot lacks required permissions or can't manage `missing_role`, but saves anyway.
- **`clear`** — Remove the intros configuration for this guild (channel, required role, missing-intro role).

State is persisted to `state/intros_{guild_id}.yaml`.

### Slash Commands (`/intros`)

- **`missing`** — Lists members (filtered by required role if configured) who have not posted in the intros channel. Requires MANAGE_GUILD. Responds with @mentions of missing members, or a success message if everyone has posted. If `missing_role_id` is configured, also reconciles the role (adds it to missing members lacking it, removes it from holders who've posted); the response and log message report both counts.

### Event Listeners

- **`GuildMessageCreateEvent`** (`listeners.py`) — When a non-bot member posts in the configured intros channel, if they currently have the `missing_role`, the role is removed immediately and a cute confirmation is posted to the guild log channel. Skips silently if the channel/role is unconfigured, the message is in another channel, or the member doesn't have the role. Permission/HTTP failures are logged as warnings.

### Cron Tasks

#### Daily Cron

Runs at 9:30am UTC daily (`30 9 * * *`). `_daily_guild` fetches the member list and the channel's messages **once** and reuses them for both steps:

1. Checks bot permissions (`CHANNEL_CLEANUP_PERMS`: View Channel, Read Message History, Manage Messages, Manage Threads) in the intros channel — logs a warning to the guild log channel and returns if missing. (This gate also protects the reconcile below from acting on an empty message list when the bot can't read history.)
2. **Cleanup** (`_cleanup_messages`): iterates the fetched messages (newest-first); skips pinned; deletes any from authors no longer in the guild; deletes older duplicate posts (keeping only the newest per author). Posts separate cute log messages for departed-member and duplicate removals.
3. **Reconcile role** (`_reconcile_missing`): if `missing_role_id` is set, computes `posted_ids` from the same messages and calls `scan_intros(members=..., posted_ids=...)` to two-way sync the role over eligible members — adds it to stragglers, strips it from eligible holders who've posted. Logs a summary to `gc.log()` if any roles changed or role management failed.

#### Weekly Naughty List Cron

Runs at 8:15pm UTC Saturday (`15 20 * * 6` = noon PST / 1pm PDT). For each configured guild:

1. Checks that `channel_id` is configured — skips if not.
2. Reads `GuildState.general_channel_id` via `bot.state(guild_id)` — skips if not set.
3. Re-runs `scan_intros()` (same two-way reconciliation as the daily cron): adds the `missing_role` to new stragglers and strips it from holders who've posted.
4. If nobody missing: posts an all-clear celebration message to the general channel.
5. If members missing: posts @mentions with a "naughty list" message to the general channel, noting that they're wearing the `missing_role`.
6. Logs summary to `gc.log()` including the count and any role additions/removals.

### File Structure

- **`__init__.py`** — Extension entry point (lightbulb Loader), registers `/intros` command group
- **`cron.py`** — Daily cron (`_daily_guild` → single fetch → `_cleanup_messages` + `_reconcile_missing`) and weekly naughty list cron; shared `scan_intros()` helper (accepts optional pre-fetched `members`/`posted_ids`) that classifies eligible members via `_classify_members` and two-way syncs the `missing_role` via `_sync_missing_role` / `_set_role`
- **`listeners.py`** — `GuildMessageCreateEvent` listener that removes the `missing_role` live when a flagged member posts
- **`commands.py`** — `/intros missing` slash command (uses `scan_intros()`)
- **`config.py`** — `/config intros set|clear` command registration
- **`models.py`** — Pydantic model: `IntrosGuildState`
- **`state.py`** — YAML state persistence (load/save with in-memory cache)

### Required Discord Permissions

- `READ_MESSAGE_HISTORY` — to iterate messages in the intros channel
- `MANAGE_MESSAGES` — to delete departed members' intro posts
- `MANAGE_ROLES` (+ role hierarchy) — only if `missing_role_id` is configured; needed to add the missing-intro role (daily/weekly) and remove it live (listener)

### Logging

- **Info:** Config changes, `/intros missing` results, individual intro deletions, daily/weekly role additions and removals, live role removals
- **Debug:** Cron tick, per-guild scan start
- **Warning:** Missing channel permissions (cron), permission errors on delete, role add/remove failures

Log message emojis: `📋` config / weekly check, `🧹` departed-post removal, `✂️` duplicate removal, `🏷️` daily role add, `🧽` daily role removal (safety-net), `📝` live role removal on post, `⚠️` warnings, `👀` missing command result.
