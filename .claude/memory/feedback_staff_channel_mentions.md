---
name: Staff channel messages use @mentions
description: Messages sent to the staff/log channel must use Discord @mention format for user names, not bare usernames
type: feedback
---

Messages to the staff channel must include user names in @mention format (e.g., `<@user_id>`), not bare usernames.

**Why:** Bare usernames are harder to identify and don't create clickable links or notifications in Discord.

**How to apply:** Whenever sending a message to the guild's log/staff channel (e.g., via `gc.log()`), format user references as Discord mentions (`<@{user_id}>`) rather than plain text usernames.
