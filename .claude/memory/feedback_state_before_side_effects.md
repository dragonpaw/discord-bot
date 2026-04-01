---
name: State cleanup before outbound side effects
description: Always mutate and save state before sending messages or making API calls that could fail
type: feedback
---

When completing a multi-step action (approval, close, kick), mutate state and call `save()` **before** making outbound calls like `create_message` or `delete_channel`. Wrap the outbound calls in try/except so a failure there doesn't leave state in an inconsistent half-cleaned-up condition.

```python
# Correct order
st.members = [m for m in st.members if m.id != member_id]
state.save(st)

try:
    await bot.rest.create_message(channel=announce_id, content="Welcome!")
except hikari.HTTPError:
    logger.warning("Failed to post announcement")

await gc.delete_channel(channel_id)
```

**Why:** If the announcement `create_message` throws and state hasn't been saved yet, the member stays in the onboarding list permanently. Cleanup should be atomic and unconditional; outbound notifications are best-effort.

**How to apply:** In any approval/close/completion handler, the pattern is: do the Discord API mutations (role, nickname), then clean state, then best-effort notify.
