## Activity Tracker Plugin

Tracks member engagement across text posts, media posts, reactions, and voice channel time. Scores use exponential decay so active members stay relevant while inactive ones fade. Roles and channels can be configured with multipliers. A "lurker" role is assigned/removed daily based on score.

### Scoring

Scores are computed by `calculate_score()` in `models.py`:

- **Contribution values:** text=1.0, media=2.0, reaction=0.1, vc=0.1 (per minute)
- **Exponential decay:** `score += base_pts * 0.5^(age / half_life)`. Default half-life is 14 days.
- **Activity bonus:** more hourly buckets in the last 7 days → longer half-life via `log(recent_count + 1)`.
- **Log weighting:** older buckets within the same kind count less via `1 / log(idx + 2)` (diminishing returns).
- **Role multipliers:** best matching non-ignored RoleConfig applies `contribution_multiplier` and `decay_multiplier`.
- **`ACTIVITY_FLOOR = 0.3`** — threshold below which a member is considered a lurker.

### Bucketing

Contributions are grouped into per-hour buckets `(kind, hour_timestamp)` rather than one record per event. Reduces YAML size dramatically on active servers.

### Prune logic

Buckets are pruned per-user daily based on contribution negligibility, not a fixed age cutoff:

- A bucket is pruned when its maximum possible decayed contribution (at best-case log_weight position 0 with the user's effective half_life and contrib_mult, ignoring activity bonus) falls below `PRUNE_THRESHOLD = 0.03` (1% of `ACTIVITY_FLOOR`).
- Hard cap: buckets are always removed after `PRUNE_DAYS_MAX = 300` days regardless.
- Users with no remaining buckets have their state file deleted entirely.
- Users who have left the guild are also pruned.

This ensures scores decay naturally to the floor before data is removed — no cliffs.

### Natural floor-crossing times (approximate)

Times after last activity for score to drop below `ACTIVITY_FLOOR = 0.3`, assuming 28 days of prior activity. These are natural decay times — the prune does not interfere.

| User type        | Preset   | Activity pattern   | ~T_floor  |
| ---------------- | -------- | ------------------ | --------- |
| Slow Contributor | Standard | 5 text/week        | ~55 days  |
| 1 msg/day        | Standard | 1 text/day         | ~67 days  |
| Media Maven      | Standard | 10 media/2 weeks   | ~69 days  |
| Burst Creator    | Standard | 50 posts in 2 days | ~82 days  |
| Chatty Daily     | Standard | 100 msg/day        | ~128 days |
| VC Regular       | Active   | 2h VC + chat/day   | ~91 days  |
| Heavy user       | Veteran  | 50 posts/day       | ~124 days |

### Event Listeners

All three listeners skip bots. If a channel's `point_multiplier` is 0, the event is silently ignored for all contribution types. Activity is recorded for all members regardless of role, including immune/ignored roles and the guild owner.

- **`GuildMessageCreateEvent`** — skips bots, skips members with no roles (not through onboarding). Kind: `ContributionKind.MEDIA` if message has attachments/URLs/stickers, else `ContributionKind.TEXT`. Amount = per-channel `point_multiplier` (default 1.0).
- **`GuildReactionAddEvent`** — same guards. Kind: `ContributionKind.REACTION`. Amount = per-channel `point_multiplier` (default 1.0).
- **`VoiceStateUpdateEvent`** — join stores timestamp in `_vc_sessions`; leave computes minutes and records kind `ContributionKind.VC` if ≥1 min. Amount = `minutes × point_multiplier` (default 1.0). Switch channel = leave + join.

All contributions log `logger.debug("Activity recorded", user=..., kind=..., raw_points=...)`.

### Role Presets

Configured via `/config activity role-add` with a Discord choices dropdown:

| Preset                | `contribution_multiplier` | `decay_multiplier` | `ignored` | ~T_floor (moderate use) |
| --------------------- | ------------------------- | ------------------ | --------- | ----------------------- |
| Standard              | 1.0                       | 1.0                | False     | ~67 days                |
| Active                | 1.1                       | 1.3                | False     | ~91 days                |
| Veteran               | 1.2                       | 1.7                | False     | ~124 days               |
| Ignore (staff/exempt) | 1.0                       | 1.0                | True      | —                       |

Ignored roles are silently skipped at event time — activity is never recorded.

### Config Commands (`/config activity`)

All require guild owner.

- **`role-add @role [preset]`** — Add or update a role's activity preset.
- **`role-remove @role`** — Remove a role's activity configuration.
- **`channel-add #channel [multiplier]`** — Add or update a channel's point multiplier (default 2.0). Multiplier 0 silently ignores all activity (messages, reactions, VC) in that channel.
- **`channel-remove #channel`** — Remove a channel's multiplier.
- **`lurker @role`** — Set the lurker role (omit to clear). Validates bot can manage the role via `check_role_manageable`.
- **`status`** — Show current configuration: roles, channel multipliers, lurker role, total tracked members.

### Slash Commands (`/activity`)

`/activity report` requires the `activity_viewer_only` hook: passes if the invoker has `ADMINISTRATOR` or `MANAGE_GUILD`, or has the configured viewer role. Fails if no viewer role is set and the user lacks those permissions.

- **`score [user]`** — Show activity score for a member (defaults to self). Any member can check their own score without restriction. Checking another member's score requires the viewer role or admin/manage-guild permission. Responds ephemerally with score, status (🐉 Active / 💤 Lurking / 🛡️ Immune), bucket count, role info, and a stacked bar chart image of daily activity over the past 60 days. Guild owner always shows 🛡️ Immune (Guild Owner).
- **`report`** — Requires viewer permission. Show all non-bot members sorted alphabetically by display name. Each member gets an emoji badge: 🥇🥈🥉 for top 3 by score, 🐉 active, 💤 lurker, 🛡️ immune. Immune members (ignored role or guild owner) show their score. Members with no activity data appear with score 0.00 (lurker).

### Cron Tasks

- **On shutdown (`StoppingEvent`):** `on_stopping` — flushes all remaining dirty in-memory user state to disk before the bot exits.
- **Hourly (`:20`):** `activity_flush` — flushes dirty in-memory user state to disk.
- **Daily (4:15am UTC):** `activity_daily_cron`:
  1. **Prune:** Per-user contribution-based bucket pruning (see above). Remove departed users.
  2. **Lurker sync:** For each non-bot, non-ignored, non-owner member: compute score. Assign lurker role if `score < ACTIVITY_FLOOR` and not already lurker. Remove lurker role if `score >= ACTIVITY_FLOOR` and currently lurker. Logs role changes to `gc.log()`. Skipped if total guild bucket count < 168 (7 days × 24h — not enough history yet). Guild owner is always skipped.

### State

Split across two file types per guild:

- **`state/activity_config_{guild_id}.yaml`** — `ActivityGuildMeta`: guild_id, guild_name, config (ActivityGuildConfig with role_configs, channel_configs, lurker_role_id/name, viewer_role_id/name)
- **`state/activity_user_{guild_id}_{user_id}.yaml`** — `UserActivity`: user_id, list of ContributionBuckets

Old combined `state/activity_{guild_id}.yaml` files are automatically migrated on first load.

Dirty tracking is per-user: only modified user files are written during the hourly flush.

### File Structure

- **`__init__.py`** — Extension entry point, `/activity` command group
- **`listeners.py`** — Event listeners for message, reaction, and voice tracking; module-level `_vc_sessions` state
- **`models.py`** — Pydantic models, `ContributionKind` enum, `calculate_score()`, `bucket_is_negligible()`, `best_role_config()`, `has_ignored_role()`
- **`state.py`** — Per-user YAML persistence: `load_config`, `save_config`, `load_user`, `save_user`, `delete_user`, `list_user_ids`, `mark_user_dirty`, `flush_dirty`
- **`commands.py`** — `/activity score` and `/activity report`
- **`config.py`** — `/config activity` subcommands
- **`cron.py`** — Hourly flush + daily prune + lurker sync tasks

### Logging

- **Info:** Score checks, report views, config changes, lurker role add/remove
- **Debug:** Per-contribution saves with raw_points, state loads, cron ticks
- **Warning:** Permission failures (lurker role assignment)

All log messages use structlog with `guild=` and `user=` keyword arguments.

## Example users

**PARTICIPATION DECAY PROFILES**

📊 **Chatty Daily** (100 msgs/day)
Posts constantly throughout the day.

🐢 **Slow Contributor** (5 msgs/week)
Casual poster.

🎤 **VC Regular** (2 hrs VC + chat, Contributor role)
Active in voice.

💥 **Burst Creator** (50 posts in 2 days, then quiet)
Intense spurts followed by silence.

🎨 **Media Maven** (10 media posts/2 weeks)
Shares cool stuff regularly.

**INTERACTION VALUES:**
💬 Text Post = 1 point
🖼️ Media Post = 2 points
⭐ Reaction = 0.1 points
🔊 VC Time = 0.1 points/min (120 min = 12 points)

**ACTIVE FLOOR:** 0.3 points. Drop below this and you're no longer ranked.
**KEY:** Stay consistent. One burst fades faster than steady engagement.
