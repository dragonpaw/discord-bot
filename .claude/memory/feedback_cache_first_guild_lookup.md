---
name: Cache-first guild lookup in event listeners
description: Event listeners must use cache-first guild lookup, not bare REST fetch
type: feedback
---

In event listeners, always look up the guild cache-first before falling back to REST:

```python
bot.cache.get_guild(event.guild_id) or await bot.rest.fetch_guild(event.guild_id)
```

Never use bare `await bot.rest.fetch_guild(event.guild_id)` in a listener.

**Why:** REST fetches are unnecessary network calls when the cache should have the guild (the bot is in it to receive the event). The `media_channels` plugin establishes this as the project convention.

**How to apply:** Any time a listener or cron task needs a `GuildContext` from a guild ID, use the cache-first pattern. The cron task pattern (`bot.cache.get_guilds_view().values()`) is already correct — this applies specifically to event listeners that receive a `guild_id` and need to fetch the full guild object.
