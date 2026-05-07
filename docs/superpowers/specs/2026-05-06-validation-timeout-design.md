# Validation Timeout Design

**Date:** 2026-05-06

## Overview

Add a hard 7-day timeout to the member validation flow. Members who haven't completed onboarding within 7 days of joining are kicked and their validate channel is closed. Reminders fire every 18 hours (down from 24h) so pings vary naturally across local times based on each member's join time.

## Scope

Changes are confined to the `plugins/validation/` plugin: `models.py`, `cron.py`, `config.py`, and `CLAUDE.md`. No other plugins are affected.

## Constants

Two module-level constants replace the old `max_reminders` config knob:

```python
REMINDER_INTERVAL_HOURS = 18
MAX_VALIDATION_DAYS = 7
```

## Data Model Changes

- Remove `max_reminders: int` from `ValidationGuildState`.
- `ValidationMember.reminder_count` is unchanged — reinterpreted as "number of 18h pings fired so far."
- Existing YAML state files with a `max_reminders` key load cleanly (Pydantic ignores unknown fields).

## Config Command Changes

- `/config validation setup` drops the `max_reminders` option parameter.

## Cron Logic

The hourly cron iterates every guild, then every member. For each member:

**Skip if `AWAITING_STAFF`** — staff handles these manually; no timeout, no pings.

**For `AWAITING_RULES` and `AWAITING_PHOTOS`:**

1. **Deadline check** (`now >= joined_at + 7 days`):
   - Kick the member (log warning on 404 — already left).
   - If `channel_id` is set, call `_close_validate_channel` with a dragon-voiced timeout notice.
   - Mark user ID for removal from state.

2. **Reminder check** (else if `now >= joined_at + 18h × (reminder_count + 1)`):
   - `AWAITING_RULES`: ping member in the lobby channel.
   - `AWAITING_PHOTOS`: ping member in their validate channel, nudging them to submit photos.
   - Increment `reminder_count`.

State is saved after processing each guild (unchanged from today). Each guild is wrapped in its own `try/except` so one failure doesn't abort others.

## Edge Cases

| Situation | Handling |
|---|---|
| Kick returns 404 (member left) | Log warning; still remove from state and close channel if present |
| No lobby channel configured | Skip guild entirely (unchanged) |
| `AWAITING_PHOTOS` member missing `channel_id` | Skip validate-channel ping/close; still kick |
| Startup after bot downtime | Reconciliation unchanged; cron catches expired deadlines on next tick |

## Out of Scope

- Configurable deadline (hardcoded for now; can be added later)
- `AWAITING_STAFF` timeout (staff handles manually)
- Any changes to approval, rejection, or channel-creation flows
