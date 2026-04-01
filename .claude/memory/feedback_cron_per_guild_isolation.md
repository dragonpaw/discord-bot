---
name: Per-guild try/except in cron tasks
description: Each guild's processing in a cron task must be isolated so one failure doesn't kill the rest
type: feedback
---

Wrap each guild's processing block in a `try/except Exception` so a single guild failure doesn't abort the entire cron run for all other guilds:

```python
for guild in guilds:
    try:
        # all per-guild work here
    except Exception:
        logger.exception("Error in [task] cron for guild", guild=guild.name)
```

**Why:** All existing cron tasks in the project (`birthdays`, `media_channels`, `channel_cleanup`, `subday`) use this isolation pattern. Missing it means one bad guild state or transient API error silently skips all subsequent guilds.

**How to apply:** Any new `@loader.task` that iterates over guilds must wrap the guild body in try/except.
