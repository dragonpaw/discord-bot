# Validation: Member Leave Cleanup & Startup Reconciliation

**Date:** 2026-04-21

## Problem

If a member leaves the server during the onboarding/validation flow, their private validate channel and state entry are left orphaned. Similarly, if the bot is offline when a member leaves (or when staff manually deletes a validate channel), the state is stale until the next garbage-collection opportunity.

## Solution Overview

Two additions to `dragonpaw_bot/plugins/validation/`:

1. **`MemberDeleteEvent` listener** — cleans up immediately when a member leaves mid-onboarding.
2. **`StartedEvent` listener** — reconciles all in-progress validations against live Discord state on every bot startup.

---

## 1. MemberDeleteEvent Listener (`on_member_leave`)

**File:** `commands.py`

**Trigger:** `hikari.MemberDeleteEvent`

**Behavior:**

1. Load state for the guild. Look up the member by `event.user_id`.
2. If not found in state, return (not in onboarding, nothing to do).
3. Remove the entry from `st.members` and save state (state before side effects).
4. Log to staff channel: e.g. `"*sad snort* <@id> flew away before finishing onboarding — cleaning up! 🐉"`
5. If `member_entry.channel_id` is set, fire `_close_validate_channel` as a background task (existing helper, 30s delay) with notice: `"*sad snort* Looks like they flew away before finishing! This channel will be deleted in 30 seconds~ 🐉"`

**Notes:**
- `event.old_member` may be `None` (cache miss). Use `event.user_id` for the `<@id>` mention in all messages. Use `event.old_member.display_name` for structured log fields if available, else fall back to the user ID string.
- The 30s delay uses the existing `CHANNEL_CLOSE_DELAY` constant and `_close_validate_channel` helper — no new delay logic.
- State is saved before any outbound API calls (per project convention).

---

## 2. Startup Reconciliation (`on_startup_reconcile`)

**Files:** `state.py` (new helper), `commands.py` (new listener)

**Trigger:** `hikari.StartedEvent`

### `state.py` addition: `all_guild_ids()`

A small helper that globs `STATE_DIR` for `validation_*.yaml` files and yields the integer guild IDs from their filenames. Used by the startup listener to iterate all guilds without assuming which guilds the bot is in.

### `commands.py` listener behavior:

1. Call `validation_state.all_guild_ids()` to get all guilds with persisted state.
2. Wrap each guild in its own `try/except` so one failure doesn't abort others.
3. Load state for the guild. Skip if no members in onboarding.
4. For each `ValidationMember` in state:
   - **Member still in guild?** REST-fetch `(guild_id, user_id)`. On `NotFoundError`: member left while bot was offline → remove from state, fire `_close_validate_channel` as a background task (30s delay, same as the live-leave handler) with notice: `"*sad snort* Looks like they flew away while I was napping! This channel will be deleted in 30 seconds~ 🐉"`
   - **Channel still exists?** If member is present and `channel_id` is set, REST-fetch the channel. On `NotFoundError`: staff deleted it → remove from state (nothing to delete).
5. If any entries were removed, save state and log each cleanup to the staff channel.

**Log messages:**
- Member left offline: `"*sad snort* <@id> left the server while I was napping — cleaning up their onboarding! 🐉"`
- Channel gone: `"*confused sniff* Validate channel for <@id> is gone — cleaned up their onboarding entry! 🐉"`

---

## Files Changed

| File | Change |
|------|--------|
| `dragonpaw_bot/plugins/validation/state.py` | Add `all_guild_ids()` generator |
| `dragonpaw_bot/plugins/validation/commands.py` | Add `on_member_leave` and `on_startup_reconcile` listeners |
| `dragonpaw_bot/plugins/validation/CLAUDE.md` | Update to document member-leave cleanup and startup reconciliation |

---

## What Is Not Changed

- No changes to the approval flow, rejection flow, or reminder/kick cron.
- No new delay constants — reuses existing `CHANNEL_CLOSE_DELAY = 30` and `_close_validate_channel` in all three cleanup paths.
- No changes to state schema or models.
