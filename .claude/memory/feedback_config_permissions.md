---
name: Config command permissions
description: All /config subcommands use owner_only hook unless explicitly told otherwise
type: feedback
---

All `/config` subcommands use the `guild_owner_only` hook from `context.py`, which checks `MANAGE_GUILD` (or `ADMINISTRATOR`). The hook raises `NotConfigAdmin` on failure; the global error handler in `bot.py` catches it and responds with the cute dragon denial message.

**Why:** User initially asked for guild-owner-only, then changed to MANAGE_GUILD so server admins can also use config commands. `lightbulb.prefab.owner_only` is for the Discord app/bot owner — not the server owner.

**How to apply:** Import `guild_owner_only` from `dragonpaw_bot.context` and set `hooks=[guild_owner_only]` on any `/config` subcommand class. The cute denial message is handled centrally in `bot.py`'s error handler — no per-command handling needed.
