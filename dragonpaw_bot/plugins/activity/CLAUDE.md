## Activity Tracker Plugin

Tracks member engagement across text posts, media posts, reactions, and voice channel time. Scores use exponential decay so active members stay relevant while inactive ones fade. Roles and channels can be configured with multipliers. A "lurker" role is assigned/removed daily based on score.

### Scoring

Scores are computed by `calculate_score()` in `models.py`:

- **Contribution values:** text=1.0, media=2.0, reaction=0.1, vc=0.1 (per minute)
- **Exponential decay:** `score += base_pts * 0.5^(age / half_life)`. Default half-life is 7 days.
- **Activity bonus:** more hourly buckets in the last 7 days → longer half-life via `log(recent_count + 1)`.
- **Log weighting:** older buckets within the same kind count less via `1 / log(idx + 2)` (diminishing returns).
- **Role multipliers:** best matching non-ignored RoleConfig applies `contribution_multiplier` and `decay_multiplier`.
- **`ACTIVITY_FLOOR = 0.1`** — threshold below which a member is considered a lurker.

### Bucketing

Contributions are grouped into per-hour buckets `(kind, hour_timestamp)` rather than one record per event. Max ~720 buckets/user/type over 30 days. Reduces YAML size dramatically on active servers.

### Event Listeners

- **`GuildMessageCreateEvent`** — skips bots, skips members with no roles (not through onboarding), skips ignored roles. Applies per-channel `point_multiplier`. Kind: "media" if message has attachments/URLs/stickers, else "text".
- **`GuildReactionAddEvent`** — same guards. Always kind="reaction", amount=1.0.
- **`VoiceStateUpdateEvent`** — join stores timestamp in `_vc_sessions`; leave computes minutes and records kind="vc" if ≥1 min. Switch channel = leave + join.

All contributions log `logger.debug("Activity recorded", user=..., kind=..., raw_points=...)`.

### Role Presets

Configured via `/config activity role-add` with a Discord choices dropdown:

| Preset | `contribution_multiplier` | `decay_multiplier` | `ignored` |
|---|---|---|---|
| Standard | 1.0 | 1.0 | False |
| Active | 1.1 | 1.3 | False |
| Veteran | 1.2 | 1.5 | False |
| Ignore (staff/exempt) | 1.0 | 1.0 | True |

Ignored roles are silently skipped at event time — activity is never recorded.

### Config Commands (`/config activity`)

All require guild owner.

- **`role-add @role [preset]`** — Add or update a role's activity preset.
- **`role-remove @role`** — Remove a role's activity configuration.
- **`channel-add #channel [multiplier]`** — Add or update a channel's point multiplier (default 2.0).
- **`channel-remove #channel`** — Remove a channel's multiplier.
- **`lurker @role`** — Set the lurker role (omit to clear). Validates bot can manage the role via `check_role_manageable`.
- **`status`** — Show current configuration: roles, channel multipliers, lurker role, total tracked members.

### Slash Commands (`/activity`)

Both require `MANAGE_GUILD`.

- **`score [user]`** — Show activity score for a member (defaults to self). Responds ephemerally with score, status (active/lurking), bucket count, and role info.
- **`leaderboard [count]`** — Show top N members by score (default 10, max 25). Skips bots and ignored-role members.

### Cron Task

Runs daily at 4am UTC (`0 4 * * *`):

1. **Prune:** Remove buckets older than 30 days. Remove users with no remaining buckets or who have left the guild.
2. **Lurker sync:** For each non-bot, non-ignored member: compute score. Assign lurker role if `score < ACTIVITY_FLOOR` and not already lurker. Remove lurker role if `score >= ACTIVITY_FLOOR` and currently lurker. Logs role changes to `gc.log()`.

### State

Persisted as `state/activity_{guild_id}.yaml`.

- `guild_id`, `guild_name`
- `config` — `ActivityGuildConfig`: role_configs, channel_configs, lurker_role_id/name
- `users` — dict of user_id → `UserActivity` (list of `ContributionBucket`)

### File Structure

- **`__init__.py`** — Extension entry point, event listeners, `/activity` command group
- **`models.py`** — Pydantic models, `calculate_score()`, `best_role_config()`, `has_ignored_role()`
- **`state.py`** — YAML state persistence (load/save with in-memory cache)
- **`commands.py`** — `/activity score` and `/activity leaderboard`
- **`config.py`** — `/config activity` subcommands
- **`cron.py`** — Daily prune + lurker sync task

### Logging

- **Info:** Score checks, leaderboard views, config changes, lurker role add/remove
- **Debug:** Per-contribution saves with raw_points, state loads, cron ticks
- **Warning:** Permission failures (lurker role assignment)

All log messages use structlog with `guild=` and `user=` keyword arguments.
