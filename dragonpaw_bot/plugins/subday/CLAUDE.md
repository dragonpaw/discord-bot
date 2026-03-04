## SubDay Plugin — "Where I am Led"

A 52-week guided journal program for submissives. Participants sign up, receive weekly prompts via DM, and get marked complete by reviewers. Milestones at weeks 13/26/39/52 earn roles and prizes.

### Per-Server Configuration

All settings are configurable per server via `/subday config`, `/subday prize-roles`, and `/subday prizes` (owner only). Settings are stored in the guild's `SubDayGuildState` and persisted to YAML. Old state files without a `config` key automatically get defaults via Pydantic.

All role fields default to None (guild-owner-only access) and channel fields default to None (disabled) until configured. `enroll_role` supports multiple roles (OR logic: any match grants access); old single-string values are automatically migrated to a list.

The guild owner always passes role permission checks (enroll, complete, backfill), regardless of whether they have the required role.

Notifications (completions, milestones, signups, removals, owner accept/deny, config changes) are sent to the guild-wide log channel configured via `/logging`.

### Slash Commands (`/subday`)

- **help** — Shows contextual help listing only the commands the user can access based on their roles and permissions.
- **about** — Displays program info as three color-coded embeds (violet intro, cyan details, yellow rewards). Includes a "Sign Up" button. Ephemeral.
- **status** — Shows the user's own progress: current week, completion status, next milestone, signup date. If the user is an owner, also shows compact status embeds (cyan) for each of their subs.
- **owner [@user]** — Sets or clears the user's owner. See Owner Feature below.
- **signup** — Requires `enroll_role`. Registers user, DMs week 1 prompt + rules. Handles DM failures gracefully.
- **complete @user [week:\<n\>]** — Requires `complete_role`. Marks the user's current week done. Cannot complete yourself. DMs a completion embed with star chart. Posts to `achievements_channel` if set. At milestones: assigns role, logs prize info. With optional `week` parameter: requires `backfill_role`, sets the participant to that week and marks it complete. Auto-enrolls the user if not signed up.
- **list** — Requires `complete_role`. Shows all participants + progress with status icons.
- **remove @user** — Requires `complete_role`. Removes a participant.
- **config** — Owner only. Shows current settings with interactive select menus. Changes save immediately on each selection.
- **prize-roles** — Owner only. Shows 4 role select menus (one per milestone week). Deselect to disable role assignment for that milestone.
- **prizes** — Owner only. Sets milestone prize descriptions. With no options, shows current prizes.

### Config Settings

**Roles and channels** (`/subday config`):

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| Enroll role(s) | Role select (multi) | _None (owner-only)_ | Roles allowed to sign up |
| Complete role | Role select | _None (owner-only)_ | Role allowed to complete/list/remove |
| Backfill role | Role select | _None (owner-only)_ | Role allowed to backfill weeks |
| Achievements channel | Channel select | _None (disabled)_ | Channel for public achievement posts |

**Milestone roles** (`/subday prize-roles`):

| Week | Default Role |
|------|-------------|
| 13 | `SubChallenge: 13wks` |
| 26 | `SubChallenge: 26wks` |
| 39 | `SubChallenge: 39wks` |
| 52 | `SubChallenge: 52wks` |

Setting a role to `None` disables role assignment for that milestone (the achievement embed still posts).

**Milestone prizes** (`/subday prizes`):

| Week | Default Prize |
|------|--------------|
| 13 | a $25 gift card |
| 26 | a tail plug or $60 equivalent |
| 39 | a Lovense toy or $120 equivalent |
| 52 | a fantasy dildo or flogger (up to $180) |

### Weekly Flow

1. User signs up → gets a welcome embed (cyan) + week 1 prompt DM'd
2. User completes the week's writing and shows a reviewer
3. Reviewer runs `/subday complete @user` → achievement posted, week marked done
4. On Sunday at 14:00 UTC, the cron task (`__init__.py`) advances completed participants to the next week and DMs a greeting embed (with progress bar and milestone countdown) followed by the prompt embed. If the participant has a confirmed owner, the owner also receives a copy. Errors are isolated per-guild. The cron also verifies owners are still in the guild and clears `owner_id` on all their subs if they've left.
5. Participants who haven't completed their week are paused (skipped until completed)

### Achievement Embeds

Controlled by `achievements_channel`. When `None` (default), channel posts are suppressed. DMs, milestone roles, and guild log messages are always sent regardless. Post failures are logged as warnings but do not block completion.

- **Regular completion** — Purple embed with star emoji + star chart image
- **Milestone (13/26/39)** — Gold embed, role assignment + star chart image
- **Graduation (52)** — Magenta embed, role assignment + star chart image

### Star Chart Image

Achievement embeds and `/subday status` include a Pillow-generated star chart PNG (`hikari.Bytes`) that mirrors the physical "Subday Journals" tracking card:

- **Title bar**: "Subday Journals:" in DaxCondensed-Bold + username in hot pink Caveat handwriting font
- **Grid layout**: 7 columns × 8 rows, divided into 4 sections of 14 cells (13 weeks + 1 prize cell)
- **Completed weeks**: Filled 5-point star in a random bright color with slight rotation/position jitter
- **Current week**: Blue outlined star
- **Future weeks**: Light gray outlined star
- **Prize cells** (cell 14 per section): Gold star when milestone reached, empty outline otherwise
- Colors and rotations are seeded by username for consistency across renders
- Fonts in `fonts/` directory: DaxCondensed (Bold/Regular/Medium) + Caveat-Bold

### Owner Feature

Submissives can register an owner via `/subday owner @user`. The owner receives copies of the sub's weekly prompts each Sunday and can see their subs' progress via `/subday status`.

**State fields** on `SubDayParticipant`:

- `owner_id: int | None` — confirmed owner's Discord user ID
- `pending_owner_id: int | None` — awaiting approval

**Flow:**

1. Sub runs `/subday owner @user` → bot DMs the target with Accept/Decline buttons
2. Owner clicks Accept → `owner_id` is set, sub is notified via DM
3. Owner clicks Decline → `pending_owner_id` is cleared, sub is notified

**Button custom IDs:** `subday_owner_request:approve|deny:{guild_id}:{sub_user_id}` — guild_id is embedded because buttons are clicked in DMs where `interaction.guild_id` is None.

**Edge cases:**

- New request while one is pending → overwrites `pending_owner_id`, sends new DM
- Target not a member of this guild → rejected ("not a member of this server")
- Request to current confirmed owner → rejected ("already your owner")
- Owner clicks stale button → `pending_owner_id` won't match → "request no longer valid"
- Owner clicks Accept twice → idempotent: "you're already their owner"
- Owner DMs disabled (request) → `ForbiddenError` caught, `pending_owner_id` rolled back
- Owner DMs disabled (Sunday prompt) → warning logged, sub's prompt unaffected
- Participant removed → cleanup loop clears `owner_id`/`pending_owner_id` references
- Owner leaves guild → Sunday cron and approval handler both verify guild membership

### File Structure

| File | Purpose |
|------|---------|
| `__init__.py` | Extension entry point (lightbulb Loader), component interaction listener with error boundaries, Sunday cron task |
| `chart.py` | Star chart image generation (Pillow) |
| `commands.py` | All slash commands, achievement embeds, milestone logic, config/prizes commands, component interaction handlers |
| `constants.py` | Shared constants: `TOTAL_WEEKS`, `MILESTONE_WEEKS`, `WEEKS_DIR`, interaction ID prefixes |
| `models.py` | Pydantic models: `SubDayParticipant`, `SubDayGuildConfig`, `SubDayGuildState` |
| `prompts.py` | Parses weekly markdown files, builds prompt embeds |
| `state.py` | YAML state persistence (load/save/cache) |
| `weeks/` | 52 weekly prompt files, `rules.md` |

### State

Persisted as `state/subday_{guild_id}.yaml`, separate from the main guild state. The `config` key stores per-server settings; old files without it get Pydantic defaults automatically.

### Error Handling

- **Interaction listener** (`__init__.py`): Both signup and config interaction handlers are wrapped in try/except. On failure, an ephemeral error message is sent to the user (with a fallback if the interaction already expired).
- **Guild log channel**: All notifications use `utils.log_to_guild()`, which silently skips if no log channel is configured and handles HTTP errors gracefully.
- **Achievement posts**: Wrapped in try/except so channel permission issues don't crash the completion flow.
- **Channel permission checks** (`utils.py`): `check_channel_perms` handles both `ForbiddenError` (can't view channel) and `NotFoundError` (channel deleted) gracefully.
- **Sunday cron task**: Per-guild error isolation — one guild's failure doesn't abort processing for other guilds.

### Required Discord Setup

- Roles: Milestone roles as configured via `/subday prize-roles`, plus any permission roles configured via `/subday config`
- Channels: `achievements_channel` via `/subday config`; guild-wide log channel via `/logging`
