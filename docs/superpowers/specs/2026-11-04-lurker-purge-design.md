# Automatic lurker purges — design

## Why

Today a lurker purge is entirely manual. A staffer drafts a list of quiet
members, the other staff strike off anyone they know has a good reason to be
quiet (a new baby, the partner of a very active member), and everyone left on
the list is kicked. This feature has the bot run that same process on a
schedule, so it actually happens regularly and nobody has to assemble the list
by hand.

Exceptions are driven by what **staff** know about a person, not by the
lurker asking. Lurkers are deliberately **not warned**: a purge is a surprise,
so that someone who says "hi" once a quarter can't game their way off the list.

## Goals

- A guild owner can turn on recurring purges and tune how often they happen
  and how long staff have to grant exceptions.
- The bot drafts the list, staff spare people from it, and a staff member
  makes the final call to kick.
- Kicked members get a warm goodbye, not a silent removal.

## Non-goals

- Warning lurkers, or letting them opt out.
- Inviting kicked members back (no invite link in the goodbye).
- Un-sparing. Sparing is one-way.
- Recording a reason for a spare. The journal entry records only who spared
  whom; the dropdown pick is the whole interaction, with no follow-up box.
- Changing how the activity score is computed.
- A server-wide shared staff role. Each plugin still keeps its own; this one
  gets its own too.

## Anti-goals (would be a failure even if it "works")

- Kicking anyone without a staff member explicitly pressing **Boot**.
- Lurkers being able to dodge the list by posting during the grace period.
- People who became lurkers after day 1 sneaking onto the current list.
- Spares from a previous purge carrying over.
- Cold or scolding copy. Staff messages and the goodbye DM use the dragon
  persona (see the root `CLAUDE.md`).

## How it works (decided with the owner)

### Configuration: `/config lurker`

A new `/config lurker` subgroup replaces `/config activity lurker`. One command
sets everything, and every option is optional, so an admin changes only what
they pass:

| Option | Meaning | Default |
|---|---|---|
| lurker role | The role the daily sync gives to inactive members (moved here from `/config activity lurker`) | unset |
| enabled | Whether automatic purges run | off |
| frequency | How often a purge starts, in whole months | 4 |
| grace | How many days staff have to spare people, in whole days | 5 |
| staff role | Who may spare people and press Boot | unset |

Turning purges **on** starts the first purge right away. After that a new purge
starts every *frequency* months, counted from when the previous one started.

The settings stay on the activity plugin's existing guild config
(`ActivityGuildConfig` in `plugins/activity/models.py`). The lurker role fields
already live there, so no state migration is needed.

### Who goes on the list

Everyone who holds the **lurker role** when the purge starts.

The daily lurker sync (`plugins/activity/cron.py`) decides who holds it. It
already skips bots, the guild owner, and members with immune ("Ignore") roles.

**New rule for the lurker sync: newcomers are never lurkers.** A member who
joined less than *frequency* months ago (by `member.joined_at`; 4 months at the
defaults) does not get the lurker role. If they already have it, the sync
removes it, like it does for immunity. New members score 0 until they post, so
without this every purge would sweep up recent joiners. Because the rule lives
in the role grant, the purge list needs no separate newcomer check.

- My call: the rule applies whether or not purges are enabled, using the
  configured frequency (default 4), so the role means the same thing either way.
  On a server that already has a lurker role, the first sync after this ships
  will remove the role from everyone who joined in the last 4 months.
- My call: in `_evaluate_lurker`'s short-circuit order it sits right after
  immunity, with its own reason (for example, `still new`) in the grouped
  staff-log message.

The list is frozen at the moment the purge starts:

- People who become lurkers later are not added.
- Posting during the grace period does not take anyone off the list.
- Only staff can remove someone, by sparing them.

### Where it happens

Everything goes to the guild's log channel (`/config channels log`), the shared
staff channel every plugin posts to via `gc.log()`.

### The flow

1. **Day 1: announcement.** The bot posts that a purge is starting, with the
   list of names and a **"Spare someone…"** dropdown.
2. **Grace days (days 1 to *grace*).** Each day the bot posts a fresh reminder
   ("N days left") with the current list and the dropdown.
   - Spared names show crossed out, with who spared them.
   - The dropdown only lists people who are still at risk, so it shrinks as
     staff spare people.
   - Only the staff role can use it.
3. **The day after the grace period.** The bot posts the final list (spared
   people crossed out) with a **Boot** button. Nothing happens until a staff
   member presses it.
4. **Boot.** For each person still on the list, the bot sends a warm goodbye
   DM, then kicks them.
5. **Expiry.** If nobody presses Boot within **2 weeks**, the purge lapses. The
   bot posts that nobody was kicked, and the next purge goes ahead on schedule.

Spares never carry over: every purge starts from a fresh list.

## Calls I made (not discussed with the owner; check these)

- **Permissions when unset.** With no staff role configured, only the guild
  owner can spare and boot. This matches how SubDay treats unset roles.
- **Refusing to enable.** Turning purges on is refused, with a friendly
  explanation, if there's no lurker role or no log channel. A purge can't work
  without either. If the bot lacks Kick Members, setup warns but still saves
  (the repo's usual permission-validation pattern).
- **Turning purges off cancels the current one.** The bot posts a note in the
  staff channel saying so.
- **One live dropdown at a time.** When the next daily reminder goes up, the
  bot strips the dropdown from the previous one, so staff always act on the
  newest post.
- **Sparing stays open next to Boot.** The final post keeps the dropdown until
  Boot is pressed, so a late "wait, not them" can still be honoured. Kicks are
  the one irreversible step here.
- **Departed members.** Anyone who has already left is skipped at Boot and
  shown as left in the final summary.
- **DM before kick.** The goodbye DM is sent before the kick, because once
  kicked the bot no longer shares a server with them and usually can't DM.
  DM failures are logged and don't block the kick.
- **Results summary.** After Boot, the bot posts who was kicked, who had left,
  and any kicks that failed (for example, because of role hierarchy), with
  actionable fix text.
- **Audit trail.** Each kick's audit-log reason names the staff member who
  pressed Boot.
- **Member journal, written directly.** Each kick and each spare is recorded in
  the member journal, written directly to the journal store (as staff-authored
  entries already are) rather than through `gc.log()`. Otherwise a Boot would
  post one staff-channel message per person. The staff channel instead gets the
  daily list posts and a single results summary after Boot. This needs new
  `EntryKind` values in `journal.py` (for example, `lurker_kicked` and
  `lurker_spared`).
- **No button-channel card.** This is staff-driven, and the repo's convention
  is that staff-driven plugins declare no `BUTTON_ENTRY`.

## Constraints

- hikari + lightbulb v3 conventions in the root `CLAUDE.md` apply.
  `code-simplicity.md` is mandatory.
- **Component handlers get no auto-defer.** The spare dropdown and the Boot
  button must respond or defer within 3 seconds before doing slow work. Boot
  especially: it sends DMs and makes kicks.
- **Discord limits.**
  - A dropdown holds at most 25 options. With a larger list, offer the first 25
    at-risk names; more appear as people are spared.
  - A message holds at most 5 component rows.
- **Clicks route through `bot.py`'s prefix dispatcher.** The new `custom_id`s
  go in the activity plugin's `INTERACTION_HANDLERS`.
- **The purge must survive restarts.** It spans days, so its state (stage,
  frozen list, spares and who granted them, the message to strip the dropdown
  from, when it started) is persisted. The activity plugin's own state files
  are the natural home.
- **Double-press safety.** Two staff pressing Boot at once must not kick twice.
  A spare arriving after Boot has run must be rejected kindly.

## Left to the builder

- **What drives the daily steps.** The existing 04:15 UTC daily cron is the
  obvious driver for the start, reminders, final post and expiry checks. Run
  them after the lurker sync, so day 1 sees a freshly synced role.
- **Day counting when enabling mid-day.** Day 1 is the day it was enabled; the
  next cron tick is day 2.
- **Exact data model and custom IDs.**
- **Copy.** All of it is in the dragon persona with unique leading emojis, per
  the repo's `gc.log()` rules.
- **What `/config lurker` shows with no options** (presumably the current
  settings and any purge in progress). Also whether `/config activity status`
  drops or points to the lurker role line.
- **Docs.** Update `plugins/activity/CLAUDE.md` and the root `CLAUDE.md`
  `/config` subgroup list.
- **Tests.** Cover at least: list freezing, spare removes the person from the
  dropdown and is one-way, Boot permission, Boot idempotency, expiry, and
  cancel on disable.
