## SubDay Plugin — "Where I am Led"

A 52-week guided journal program for submissives. Participants sign up, receive weekly prompts via DM, and get marked complete by Headmistresses. Milestones at weeks 13/26/39/52 earn roles and prizes.

### Slash Commands (`/subday`)

- **(no subcommand)** — Shows contextual help listing only the commands the user can access based on their roles and permissions.
- **about** — Displays program info from `weeks/about.md` as a rich embed.
- **status** — Shows the user's own progress: current week, completion status, next milestone, signup date.
- **signup** — Requires `Submissive` role. Registers user, DMs week 1 prompt + rules. Handles DM failures gracefully with a warning message.
- **complete @user** — Requires `Headmistress` role. Marks the user's current week done. Cannot complete yourself. Posts achievement in `#achievements` with emoji. At milestones: assigns role, pings staff.
- **list** — Requires `Headmistress` role. Shows all participants + progress with status icons.
- **remove @user** — Requires `Headmistress` role. Removes a participant.
- **setweek @user <week>** — Owner only. Sets a participant's current week (for backfilling). Auto-enrolls the user if they aren't signed up yet.

### Weekly Flow

1. User signs up → gets week 1 prompt DM'd with instructions
2. User completes the week's writing and shows a Headmistress
3. Headmistress runs `/subday complete @user` → achievement posted, week marked done
4. On Sunday at 14:00 UTC, the scheduler advances completed participants to the next week and DMs the new prompt
5. Participants who haven't completed their week are paused (skipped until completed)

### Achievement Embeds

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

| Week | Role Earned | Reward |
|------|------------|--------|
| 13 | SubChallenge: 13wks | Gift certificate |
| 26 | SubChallenge: 26wks | Gift certificate |
| 39 | SubChallenge: 39wks | Gift certificate |
| 52 | SubChallenge: 52wks | Graduation prize |

### File Structure

| File | Purpose |
|------|---------|
| `__init__.py` | Plugin entry point, load/unload, contextual `/subday` help |
| `chart.py` | Star chart image generation (Pillow) for achievement/status embeds |
| `commands.py` | All slash commands, achievement embeds, milestone logic |
| `scheduler.py` | Sunday background task: advances weeks, DMs prompts |
| `models.py` | Pydantic models: `SubDayParticipant`, `SubDayGuildState` |
| `prompts.py` | Parses weekly markdown files, builds prompt embeds |
| `state.py` | YAML state persistence (load/save/cache) |
| `weeks/` | 52 weekly prompt files, `rules.md`, `about.md` |

### State

Persisted as `state/subday_{guild_id}.yaml`, separate from the main guild state.

### Required Discord Setup

- Roles: `Submissive`, `Headmistress`, and the four `SubChallenge:` milestone roles
- Channels: `#achievements` (completion announcements), `#staff` (milestone notifications)
