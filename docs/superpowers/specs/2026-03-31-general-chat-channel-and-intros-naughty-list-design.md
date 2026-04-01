# Design: General Chat Channel + Intros Naughty List

**Date:** 2026-03-31

## Overview

Two related changes:

1. Add a bot-wide `general_channel_id` to `GuildState`, configurable via a new `/config channels` command group. Plugins that post public-facing messages use this shared channel instead of per-plugin config fields.
2. Add a weekly "naughty list" cron to the intros plugin that posts @mentions of members who haven't introduced themselves yet to the general chat channel.

---

## Part 1 — `GuildState` + `/config channels`

### `structs.py`

Add one field to `GuildState`:

```python
general_channel_id: hikari.Snowflake | None = None
```

No migration needed — it's nullable and defaults to `None`.

### `bot.py`

- Rename the `_bot_sub` subgroup from `"bot"` to `"channels"` (description: `"Channel settings"`).
- Rename `BotLogging` command to `SetLogChannel`, subcommand name `"log"`. Behavior unchanged.
- Add `SetGeneralChannel` command, subcommand name `"general"`. Same pattern as `SetLogChannel`: optional `channel` param; if provided, sets `state.general_channel_id`; if omitted, clears it. Responds ephemerally. Uses `guild_owner_only` hook.
- Update `_yaml_dict_to_state` to cast `general_channel_id` to `hikari.Snowflake` if present (same pattern as `log_channel_id`).

---

## Part 2 — Validation: remove `general_channel_id`

### `plugins/validation/models.py`

Remove `general_channel_id: int | None = None` from `ValidationGuildState`.

### `plugins/validation/commands.py` (`handle_approve_modal`)

Load `GuildState` via `bot.state(hikari.Snowflake(interaction.guild_id))` and read `general_channel_id` from it. If `None`, skip the welcome announcement (same behavior as before when it wasn't configured).

### `plugins/validation/config.py`

Remove the `general_channel_id` parameter from `/config validation setup`. Update the status display accordingly.

---

## Part 3 — Media channels: redirect defaults to general chat

No schema changes. When building the nom notice in `plugins/media_channels/__init__.py`, the redirect channel is resolved as:

1. Per-channel `redirect_channel_id` (existing behavior, takes priority)
2. `GuildState.general_channel_id` (new fallback)
3. No redirect hint (existing behavior when neither is set)

Load `GuildState` via `bot.state(guild_id)` when handling the message event to get the fallback.

---

## Part 4 — Intros: weekly naughty list cron

### Schedule

`0 20 * * 6` — Saturday 20:00 UTC (noon PST / 1pm PDT).

### Logic (in `plugins/intros/cron.py`)

New task function `intros_weekly_naughty_list`. Per guild:

1. Load `IntrosGuildState`. Skip if `channel_id` is `None`.
2. Load `GuildState` via `bot.state(guild_id)`. Skip if `general_channel_id` is `None`.
3. Fetch all messages from the intros channel; collect author IDs of non-bot posters.
4. Fetch all guild members; filter to non-bots and (if `required_role_id` is set) members with that role.
5. Find members whose ID is not in the posted set.
6. If none missing: post a celebratory dragon message to `general_channel_id`.
7. If some missing: post @mentions list with dragon-persona "naughty list" copy to `general_channel_id`.
8. Log summary to `gc.log()`.

### Copy (dragon persona)

**All clear:**
> *does a happy wiggle* 🐉 Everyone in the hoard has posted an introduction this week — I'm so proud of you all! 🐾

**Missing members:**
> *squints with clipboard* 🐉 Psst! These lovely folks haven't introduced themselves in <#intros_channel> yet — give 'em a little nudge! 🐾
> @mention1 @mention2 ...

### Error handling

Wrap each guild's body in `try/except` (already done for cleanup cron — same pattern). Log permission errors to `gc.log()` if the bot can't read the intros channel.

---

## Files Changed

| File | Change |
|------|--------|
| `dragonpaw_bot/structs.py` | Add `general_channel_id` to `GuildState` |
| `dragonpaw_bot/bot.py` | Rename `/config bot` → `/config channels`; add `SetGeneralChannel` command; update `_yaml_dict_to_state` |
| `dragonpaw_bot/plugins/validation/models.py` | Remove `general_channel_id` |
| `dragonpaw_bot/plugins/validation/commands.py` | Read `general_channel_id` from `GuildState` |
| `dragonpaw_bot/plugins/validation/config.py` | Remove `general_channel_id` param from setup |
| `dragonpaw_bot/plugins/media_channels/__init__.py` | Add general chat fallback to redirect resolution |
| `dragonpaw_bot/plugins/intros/cron.py` | Add `intros_weekly_naughty_list` cron task |
| `dragonpaw_bot/plugins/intros/CLAUDE.md` | Document naughty list cron |
| `dragonpaw_bot/plugins/validation/CLAUDE.md` | Update to reflect `general_channel_id` removal |
| `dragonpaw_bot/plugins/media_channels/CLAUDE.md` | Document general chat fallback |
