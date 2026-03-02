## SubDay Plugin — "Where I am Led"

A 52-week guided journal program for submissives. Participants sign up, receive weekly prompts via DM, and get marked complete by reviewers. Milestones at weeks 13/26/39/52 earn roles and prizes.

### Per-Server Configuration

All role permissions, channel names, and prize descriptions are configurable per server via `/subday config` (owner only). Settings are stored in the guild's `SubDayGuildState` and persisted to YAML. Old state files without a `config` key automatically get defaults via Pydantic.

**Defaults:** All fields (`enroll_role`, `complete_role`, `backfill_role`, `achievements_channel`, `staff_channel`) default to `None`, meaning owner-only access and no channel posts until configured.

### Slash Commands (`/subday`)

- **(no subcommand)** — Shows contextual help listing only the commands the user can access based on their roles and permissions.
- **about** — Displays program info from `weeks/about.md` as a rich embed.
- **status** — Shows the user's own progress: current week, completion status, next milestone, signup date.
- **signup** — Requires configured `enroll_role` (or owner). Registers user, DMs week 1 prompt + rules. Handles DM failures gracefully with a warning message.
- **complete @user** — Requires configured `complete_role` (or owner). Marks the user's current week done. Cannot complete yourself. DMs the user a completion embed with their star chart. If `achievements_channel` is set, also posts achievement there. At milestones: assigns role, pings staff (if `staff_channel` is set).
- **list** — Requires configured `complete_role` (or owner). Shows all participants + progress with status icons.
- **remove @user** — Requires configured `complete_role` (or owner). Removes a participant.
- **setweek @user <week>** — Requires configured `backfill_role` (or owner). Sets a participant's current week (for backfilling). Auto-enrolls the user if they aren't signed up yet.
- **config** — Owner only. Shows current settings as an embed with interactive role and channel select menus (dropdowns). Select a role/channel to set it; deselect to clear back to None. Changes are saved immediately on each selection.
- **prizes** — Owner only. Sets milestone prize descriptions via slash command options. All options are optional; with no options shows current prizes.

### Config Settings (`/subday config`)

The config command sends an ephemeral message with 5 select menus:

| Dropdown | Type | Default | Description |
|----------|------|---------|-------------|
| Enroll role | Role select | _None (owner-only)_ | Role allowed to sign up |
| Complete role | Role select | _None (owner-only)_ | Role allowed to complete/list/remove |
| Backfill role | Role select | _None (owner-only)_ | Role allowed to use setweek |
| Achievements channel | Channel select | _None (disabled)_ | Channel for public achievement posts |
| Staff channel | Channel select | _None (disabled)_ | Channel for staff milestone notifications |

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
4. On Sunday at 14:00 UTC, the scheduler advances completed participants to the next week and DMs the new prompt
5. Participants who haven't completed their week are paused (skipped until completed)

### Achievement Embeds

Controlled by the `achievements_channel` config field. When `None` (default), channel posts are suppressed. DMs, milestone roles, and staff notifications (if `staff_channel` is set) are always sent regardless.

- **Regular completion** — Purple embed with star emoji + star chart image
- **Milestone (13/26/39)** — Gold embed with star and sparkle emoji, role assignment, staff notification + star chart image
- **Graduation (52)** — Magenta embed with stars, sparkles, and celebration emoji, role assignment, staff notification for prize + star chart image

### Star Chart Image

Achievement embeds and the `/subday status` command include a Pillow-generated star chart PNG that mirrors the physical "Subday Journals" tracking card. Features:

- **Title bar**: "Subday Journals:" in DaxCondensed-Bold + username in hot pink Caveat handwriting font
- **Grid layout**: 7 columns × 8 rows, divided into 4 sections of 14 cells (13 weeks + 1 prize cell)
- **Completed weeks**: Filled 5-point star in a random bright color with slight rotation/position jitter (looks hand-placed like stickers)
- **Current week**: Blue outlined star
- **Future weeks**: Light gray outlined star
- **Prize cells** (cell 14 per section): Gold star when milestone reached, empty outline otherwise
- Colors and rotations are seeded by username for consistency across renders
- Fonts in `fonts/` directory: DaxCondensed (Bold/Regular/Medium) + Caveat-Bold

### Milestones

| Week | Role Earned | Default Reward |
|------|------------|----------------|
| 13 | SubChallenge: 13wks | $25 gift card |
| 26 | SubChallenge: 26wks | Tail plug or $60 equivalent |
| 39 | SubChallenge: 39wks | Lovense toy or $120 equivalent |
| 52 | SubChallenge: 52wks | Fantasy dildo or flogger (up to $180) |

### File Structure

| File | Purpose |
|------|---------|
| `__init__.py` | Plugin entry point, load/unload, component interaction listener |
| `chart.py` | Star chart image generation (Pillow) for achievement/status embeds |
| `commands.py` | All slash commands, help handler, achievement embeds, milestone logic, config/prizes commands, component interaction handler |
| `scheduler.py` | Sunday background task: advances weeks, DMs prompts |
| `models.py` | Pydantic models: `SubDayParticipant`, `SubDayGuildConfig`, `SubDayGuildState` |
| `prompts.py` | Parses weekly markdown files, builds prompt embeds |
| `state.py` | YAML state persistence (load/save/cache) |
| `weeks/` | 52 weekly prompt files, `rules.md`, `about.md` |

### State

Persisted as `state/subday_{guild_id}.yaml`, separate from the main guild state. The `config` key stores per-server settings; old files without it get Pydantic defaults automatically.

### Required Discord Setup

- Roles: The four `SubChallenge:` milestone roles, plus any roles configured via `/subday config`
- Channels: As configured via `/subday config` (`staff_channel`, optionally `achievements_channel`)
