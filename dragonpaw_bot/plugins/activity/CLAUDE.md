## Activity Tracker Plugin

Tracks member engagement across text posts, media posts, reactions, and voice channel time. Scores use exponential decay so active members stay relevant while inactive ones fade. Roles and channels can be configured with multipliers. A "lurker" role is assigned/removed daily based on score, and an optional automatic purge lets staff boot long-term lurkers (see Lurker purges).

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

- A bucket is pruned when its maximum possible decayed contribution (at best-case log_weight position 0 with the user's effective half_life and contrib_mult, ignoring activity bonus) falls below `PRUNE_THRESHOLD = 0.03` (10% of `ACTIVITY_FLOOR`).
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

Ignored roles still have their activity recorded at event time (all members do); immunity is applied when scoring and during the lurker sync.

### Config Commands (`/config activity`)

All require MANAGE_GUILD or ADMINISTRATOR (the guild_owner_only hook).

- **`role-add @role [preset]`** — Add or update a role's activity preset.
- **`role-remove @role`** — Remove a role's activity configuration.
- **`channel-add #channel [multiplier]`** — Add or update a channel's point multiplier (default 2.0). Multiplier 0 silently ignores all activity (messages, reactions, VC) in that channel.
- **`channel-remove #channel`** — Remove a channel's multiplier.
- **`viewer @role`** — Set the activity viewer role (omit to clear) — who may check others' scores and run `/activity report` without admin permissions.
- **`status`** — Show current configuration: roles, channel multipliers, lurker role (with a `(see /config lurker)` hint), total tracked members.

### Lurker Config (`/config lurker`)

A direct subcommand of `/config` (registered in `bot.py` as `activity_config.LurkerConfig`), guild_owner_only. All options are optional; with none it shows an ephemeral settings summary, including any purge in progress (day N of the grace period, or "final list is up").

| Option       | Type        | Meaning                                                            |
| ------------ | ----------- | ------------------------------------------------------------------ |
| `role`       | role        | The lurker role. Checked with `check_role_manageable`.             |
| `enabled`    | boolean     | Turn automatic purges on or off.                                   |
| `frequency`  | int, 1-12   | Months between purges (default 4). Also the newcomer window.       |
| `grace`      | int, 1-14   | Days staff have to spare members (default 5).                      |
| `staff_role` | role        | Role allowed to spare and Boot, in addition to the guild owner.    |
| `clear_role` | boolean     | Forget the lurker role; also turns purges off (cancelling any run). |

Only the options given are changed. Enabling is **refused** (old value kept, a friendly note says why) if there is no lurker role (counting one set in the same call) or no log channel (`/config channels log`). If the bot lacks `KICK_MEMBERS` the command still saves but warns. The config is saved before any purge is started. Turning purges on starts a purge immediately; if nobody is on the list the response says so and the next daily check tries again. Turning them off (with `enabled:false` or `clear_role:true`) while one is running cancels it. Changes are logged with ⚙️.

### Lurker purges

An opt-in, staff-supervised sweep of everyone who holds the lurker role. Nobody is kicked without a staff member pressing **Boot**. Logic lives in `purge.py`; the daily cron calls `purge.purge_daily_step`.

**Schedule.** `next_action(meta, now)` decides each day, from `purge_enabled`, `last_purge_started`, `purge_interval_months` and the running `purge`:

- No purge running and none started within `purge_interval_months` (or never) -> `start`.
- Running, grace days remain (`day + 1 <= purge_grace_days`) -> `remind`: increments `day` and reposts the list.
- Running, grace over, Boot not yet posted -> `final`: sets `boot_posted_at` and reposts with the Boot button.
- Boot posted `PURGE_BOOT_EXPIRY_DAYS = 14` days ago, unpressed -> `expire`: components stripped, purge cleared, nobody kicked, a log line says so.

`start_purge` stamps `last_purge_started`, freezes the candidate list, posts it and returns `True`. With no candidates it changes nothing — no post, no `last_purge_started`, no purge — and returns `False`, so the next daily cron simply tries again after a fresh lurker sync. Enabling via `/config lurker` calls it right away.

**The list is frozen at start.** Candidates are the non-bot members holding the lurker role at that moment who are not newcomers (`select_candidates(members, role_id, now, interval_months)`, sorted by display name; the same `models.is_newcomer` rule as the lurker sync, so a member who still holds a stale lurker role after joining recently is left off). Later lurkers are never added, and activity never removes anyone — a spared or newly active member stays on the list until staff act. `LurkerPurge.candidates` holds `PurgeCandidate(user_id, display_name, spared_by)`.

**The post.** One embed in the staff log channel (`build_purge_post`) listing candidates, spared ones struck through with "spared by X". Description is capped at 4096 chars with an "...and N more" tail. A select menu (`activity_purge_spare`, max 25 options of the not-yet-spared) lets staff spare members; after the final step a danger **Boot** button (`activity_purge_boot`) appears. Each repost first strips the components from the previous message (`message_id` and `channel_id` are tracked, so the strip reaches the old post even if the log channel has since changed; a `None` `channel_id` falls back to the current log channel). Member and staff display names are passed through `journal.escape_markdown` wherever they land in Discord-rendered text (embed lines, the booting line, summaries, journal summaries) — but not in select-menu labels or the kick audit reason, which don't render markdown. If no log channel is configured the post is skipped with a warning.

**Staff.** `is_purge_staff`: the guild owner, or a member with `purge_staff_role_id`. Anyone else clicking gets an in-voice refusal. Both handlers also reject clicks on a stale message (not the current `message_id`).

**Spare** (`handle_spare`): marks the chosen candidates `spared_by` the clicker's display name, saves, writes a `lurker_spared` journal entry per spare (before responding, so a failed response can't lose the record), then updates the post in place. Spares are one-way — no un-spare and no reason prompt.

**Boot** (`handle_boot`): claims the purge (`meta.purge = None`, saved) before any await so a second press is turned away, then updates the post to "booting...". If that response fails, the purge is restored and saved and the error re-raised — nobody is kicked. Then for each unspared candidate: count them as left if they're gone (REST 404 on lookup, or a 404 from the kick), DM a gentle goodbye (failure is only warned), kick with reason `Lurker purge — booted by <staff>`. Any other lookup or kick error is collected as failed and the loop carries on. Each kick writes a `lurker_kicked` journal entry. The summary goes to the log channel in order: header, couldn't kick (+ a 🔧 hint to check Kick Members and role hierarchy), Booted, Had already left. `_summary_messages` packs it into as many messages as needed, each ≤ 2000 chars, never splitting a name; a list that overflows repeats its label in the next message.

**Cancel and expire.** `cancel_purge` and the `expire` step clear the purge and save *before* awaiting anything, then strip the components and log — so a Boot click landing mid-await sees no purge.

**Custom IDs** (registered in `INTERACTION_HANDLERS`): `activity_purge_spare` -> `handle_spare`, `activity_purge_boot` -> `handle_boot`.

**Journal kinds:** `lurker_spared`, `lurker_kicked` (written directly via `journal.record`, with display names captured at list time).

### Slash Commands (`/activity`)

`/activity report` requires the `activity_viewer_only` hook: passes if the invoker has `ADMINISTRATOR` or `MANAGE_GUILD`, or has the configured viewer role. Fails if no viewer role is set and the user lacks those permissions.

- **`score [user]`** — Show activity score for a member (defaults to self). Any member can check their own score without restriction. Checking another member's score requires the viewer role or admin/manage-guild permission. Responds ephemerally with score, status (🐉 Active / 💤 Lurking / 🛡️ Immune), bucket count, role info, and a stacked bar chart image of hourly activity over the past 14 days (`MAX_HOURS` in `chart.py`). Guild owner always shows 🛡️ Immune (Guild Owner).
- **`report`** — Requires viewer permission. Show all non-bot members sorted alphabetically by display name. Each member gets an emoji badge: 🥇🥈🥉 for top 3 by score, 🐉 active, 💤 lurker, 🛡️ immune. Immune members (ignored role or guild owner) show their score. Members with no activity data appear with score 0.00 (lurker).

### Button Channel Card

Declared as `BUTTON_ENTRY` in `__init__.py` (cyan, 📊 "My Activity Score", custom_id `activity_score` → `handle_score_interaction`). Shown only when `state/activity_config_{guild_id}.yaml` exists (`state.is_configured`), i.e. an admin has configured the tracker.

The button always shows the clicker their own score, so it skips the viewer-role check that `/activity score`'s `user` option needs. Both build the embed with `build_score_embed(bot, guild_id, member)`. The handler defers first — chart rendering would otherwise blow Discord's 3-second deadline.

### Cron Tasks

- **On shutdown (`StoppingEvent`):** `on_stopping` — flushes all remaining dirty in-memory user state to disk before the bot exits.
- **Hourly (`:20`):** `activity_flush` — flushes dirty in-memory user state to disk.
- **Daily (4:15am UTC):** `activity_daily_cron`:
  1. **Prune:** Per-user contribution-based bucket pruning (see above). Remove departed users.
  2. **Lurker sync:** For each non-bot, non-owner member with at least one role, decide whether they should be a lurker. The decision short-circuits in this order:
     - **Immune** (`has_ignored_role`) → never lurker. If they currently have it, remove it with reason `gained immunity`.
     - **Newcomer** — joined within the last `purge_interval_months` (`models.is_newcomer`: `joined_at > add_months(now, -interval)`; an unknown `joined_at` counts as not new) -> never lurker. If they currently have it, remove it with reason `still new`. This stops fresh joiners being tagged, and so purged, before they have had a fair chance to be active.
     - **Activity** `score < ACTIVITY_FLOOR` → lurker with reason `no longer active`.
     - Otherwise → not a lurker; if they currently have it, remove it with reason `now active`.

     The activity plugin is concerned with activity only — missing introductions are tracked separately by the intros plugin via its own `missing_intro` role (see `plugins/intros/CLAUDE.md`). The two roles are independent: a member can be flagged by both, neither, or either one.

     Role changes are logged to `gc.log()` grouped by reason: `Added (no longer active)`, `Removed (gained immunity)`, `Removed (still new)`, `Removed (now active)`. Skipped if total guild bucket count < 168 (7 days × 24h — not enough history yet). Guild owner is always skipped.
  3. **Purge step:** when a lurker role is configured, `purge.purge_daily_step` runs after the sync (start / remind / final / expire — see Lurker purges). It runs even when the lurker sync was skipped for lack of history.

### State

Split across two file types per guild:

- **`state/activity_config_{guild_id}.yaml`** — `ActivityGuildMeta`: guild_id, guild_name, config (ActivityGuildConfig with role_configs, channel_configs, lurker_role_id/name, viewer_role_id/name, `purge_enabled` (default off), `purge_interval_months` (4), `purge_grace_days` (5), `purge_staff_role_id`), `purge` (`LurkerPurge | None`: `started_at`, `day`, `boot_posted_at`, `message_id`, `channel_id`, `candidates`), `last_purge_started`
- **`state/activity_user_{guild_id}_{user_id}.yaml`** — `UserActivity`: user_id, list of ContributionBuckets


Dirty tracking is per-user: only modified user files are written during the hourly flush.

### File Structure

- **`__init__.py`** — `INTERACTION_HANDLERS` and `BUTTON_ENTRY` exports
- **`listeners.py`** — Event listeners for message, reaction, and voice tracking; module-level `_vc_sessions` state
- **`models.py`** — Pydantic models, `ContributionKind` enum, `calculate_score()`, `bucket_is_negligible()`, `best_role_config()`, `has_ignored_role()`
- **`state.py`** — Per-user YAML persistence: `load_config`, `save_config`, `load_user`, `save_user`, `delete_user`, `list_user_ids`, `mark_user_dirty`, `flush_dirty`
- **`commands.py`** — `/activity score` and `/activity report`
- **`chart.py`** — Stacked-bar activity chart rendering (Pillow)
- **`config.py`** — `/config activity` subcommands and `/config lurker` (`LurkerConfig`)
- **`cron.py`** — Hourly flush + daily prune + lurker sync tasks; calls the purge step
- **`purge.py`** — Lurker purge: candidate selection, schedule decision (`next_action`), staff post, `start_purge`/`cancel_purge`/`purge_daily_step`, Spare and Boot handlers

### Logging

- **Info:** Score checks, report views, config changes, lurker role add/remove, purge starts/spares/boots
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
