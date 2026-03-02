## SubDay Plugin — "Where I am Led"

A 52-week guided journal program for submissives. Participants sign up, receive weekly prompts via DM, and get marked complete by reviewers. Milestones at weeks 13/26/39/52 earn roles and prizes.

### Per-Server Configuration

All role permissions, channel names, and prize descriptions are configurable per server via `/subday config` (owner only). Settings are stored in the guild's `SubDayGuildState` and persisted to YAML. Old state files without a `config` key automatically get defaults via Pydantic.

**Defaults:** All fields (`enroll_role`, `complete_role`, `backfill_role`, `achievements_channel`, `staff_channel`) default to `None`, meaning guild-owner-only access and no channel posts until configured.

**Guild owner bypass:** The server owner (guild owner) always passes role permission checks (enroll, complete, backfill), regardless of whether they have the required role.

**Admin commands:** The config, prize-roles, and prizes commands require Manage Guild permission. The config interaction handler additionally checks that the user is the server owner.

### Slash Commands (`/subday`)

- **help** — Shows contextual help listing only the commands the user can access based on their roles and permissions.
- **about** — Displays program info from `weeks/about.md` as a rich embed with `{{mustache}}` template substitution from guild config (roles, prizes, rewards). Includes a "Sign Up" button that runs the same signup logic as `/subday signup`.
- **status** — Shows the user's own progress: current week, completion status, next milestone, signup date.
- **signup** — Requires configured `enroll_role` (or owner). Registers user, DMs week 1 prompt + rules. Handles DM failures gracefully with a warning message.
- **complete @user [week:\<n\>]** — Requires configured `complete_role` (or owner). Marks the user's current week done. Cannot complete yourself. DMs the user a completion embed with their star chart. If `achievements_channel` is set, also posts achievement there. At milestones: assigns role, pings staff (if `staff_channel` is set). Logs all completions to `staff_channel` if configured. With optional `week` parameter: requires `backfill_role` instead, sets the participant to that week and marks it complete in one step. Auto-enrolls the user if they aren't signed up yet.
- **list** — Requires configured `complete_role` (or owner). Shows all participants + progress with status icons.
- **remove @user** — Requires configured `complete_role` (or owner). Removes a participant.
- **config** — Requires Manage Guild. Shows current settings as an embed with interactive role and channel select menus (dropdowns). Select a role/channel to set it; deselect to clear back to None. Changes are saved immediately on each selection. The interaction handler verifies the user is the server owner.
- **prize-roles** — Requires Manage Guild. Shows current milestone role settings with 4 role select menus (one per milestone week). Select a role to set it; deselect to disable role assignment for that milestone. When `None`, the milestone embed still posts but no role is granted.
- **prizes** — Requires Manage Guild. Sets milestone prize descriptions via slash command options. All options are optional; with no options shows current prizes.

### Config Settings (`/subday config`)

The config command sends an ephemeral message with 5 select menus:

| Dropdown | Type | Default | Description |
|----------|------|---------|-------------|
| Enroll role | Role select | _None (owner-only)_ | Role allowed to sign up |
| Complete role | Role select | _None (owner-only)_ | Role allowed to complete/list/remove |
| Backfill role | Role select | _None (owner-only)_ | Role allowed to backfill weeks via `/subday complete week:<n>` |
| Achievements channel | Channel select | _None (disabled)_ | Channel for public achievement posts |
| Staff channel | Channel select | _None (disabled)_ | Channel for staff notifications (completions and milestones) |

### Milestone Role Settings (`/subday prize-roles`)

The prize-roles command sends an ephemeral message with 4 role select menus (one per milestone week). Deselect to set `None` (no role granted at that milestone).

| Dropdown | Default |
|----------|---------|
| Week 13 milestone role | `SubChallenge: 13wks` |
| Week 26 milestone role | `SubChallenge: 26wks` |
| Week 39 milestone role | `SubChallenge: 39wks` |
| Week 52 milestone role | `SubChallenge: 52wks` |

### Prize Settings (`/subday prizes`)

| Option | Default |
|--------|---------|
| `prize_13` | a $25 gift card |
| `prize_26` | a tail plug or $60 equivalent |
| `prize_39` | a Lovense toy or $120 equivalent |
| `prize_52` | a fantasy dildo or flogger (up to $180) |

### Weekly Flow

1. User signs up → gets week 1 prompt DM'd with instructions
2. User completes the week's writing and shows a reviewer
3. Reviewer runs `/subday complete @user` → achievement posted, week marked done
4. On Sunday at 14:00 UTC, the cron task (`__init__.py`) advances completed participants to the next week and DMs the new prompt. Errors are isolated per-guild so one failure doesn't block others.
5. Participants who haven't completed their week are paused (skipped until completed)

### Achievement Embeds

Controlled by the `achievements_channel` config field. When `None` (default), channel posts are suppressed. DMs, milestone roles, and staff notifications (if `staff_channel` is set) are always sent regardless. Achievement post failures (permissions, deleted channel) are logged as warnings but do not block completion.

- **Regular completion** — Purple embed with star emoji + star chart image
- **Milestone (13/26/39)** — Gold embed with star and sparkle emoji, role assignment, staff notification + star chart image
- **Graduation (52)** — Magenta embed with stars, sparkles, and celebration emoji, role assignment, staff notification for prize + star chart image

### Star Chart Image

Achievement embeds and the `/subday status` command include a Pillow-generated star chart PNG (passed directly to `embed.set_image()` as `hikari.Bytes`) that mirrors the physical "Subday Journals" tracking card. Features:

- **Title bar**: "Subday Journals:" in DaxCondensed-Bold + username in hot pink Caveat handwriting font
- **Grid layout**: 7 columns × 8 rows, divided into 4 sections of 14 cells (13 weeks + 1 prize cell)
- **Completed weeks**: Filled 5-point star in a random bright color with slight rotation/position jitter (looks hand-placed like stickers)
- **Current week**: Blue outlined star
- **Future weeks**: Light gray outlined star
- **Prize cells** (cell 14 per section): Gold star when milestone reached, empty outline otherwise
- Colors and rotations are seeded by username for consistency across renders
- Fonts in `fonts/` directory: DaxCondensed (Bold/Regular/Medium) + Caveat-Bold

### Milestones

Milestone roles are configurable per server via `/subday prize-roles`. Setting a role to `None` disables role assignment for that milestone (the achievement embed still posts, only the role grant is skipped).

| Week | Default Role | Default Reward |
|------|-------------|----------------|
| 13 | SubChallenge: 13wks | $25 gift card |
| 26 | SubChallenge: 26wks | Tail plug or $60 equivalent |
| 39 | SubChallenge: 39wks | Lovense toy or $120 equivalent |
| 52 | SubChallenge: 52wks | Fantasy dildo or flogger (up to $180) |

### File Structure

| File | Purpose |
|------|---------|
| `__init__.py` | Extension entry point (lightbulb Loader), component interaction listener with error boundaries, Sunday cron task |
| `chart.py` | Star chart image generation (Pillow) for achievement/status embeds |
| `commands.py` | All slash commands, help handler, achievement embeds, milestone logic, config/prizes commands, component interaction handler |
| `constants.py` | Shared constants: `TOTAL_WEEKS`, `MILESTONE_WEEKS`, `WEEKS_DIR`, interaction ID prefixes |
| `models.py` | Pydantic models: `SubDayParticipant`, `SubDayGuildConfig`, `SubDayGuildState` |
| `prompts.py` | Parses weekly markdown files, builds prompt embeds |
| `state.py` | YAML state persistence (load/save/cache) |
| `weeks/` | 52 weekly prompt files, `rules.md`, `about.md` (mustache template) |

### State

Persisted as `state/subday_{guild_id}.yaml`, separate from the main guild state. The `config` key stores per-server settings; old files without it get Pydantic defaults automatically.

### Error Handling

- **Interaction listener** (`__init__.py`): Both signup and config interaction handlers are wrapped in try/except. On failure, an ephemeral error message is sent to the user (with a fallback if the interaction already expired).
- **Config log channel**: The audit message to the guild's log channel is wrapped so a deleted/inaccessible log channel doesn't prevent the config response.
- **Achievement posts**: Wrapped in try/except so channel permission issues don't crash the completion flow.
- **Channel permission checks** (`utils.py`): `check_channel_perms` handles both `ForbiddenError` (can't view channel) and `NotFoundError` (channel deleted) gracefully.
- **Sunday cron task**: Per-guild error isolation — one guild's failure doesn't abort processing for other guilds.

### Required Discord Setup

- Roles: Milestone roles as configured via `/subday prize-roles` (defaults to four `SubChallenge:` roles), plus any permission roles configured via `/subday config`
- Channels: As configured via `/subday config` (`staff_channel`, optionally `achievements_channel`)
