## Media Channels Plugin

Enforces a media-only policy in configured channels: text-only posts are automatically deleted and a brief, dragon-themed in-channel notice is shown to the author (auto-deletes after 15 seconds). Messages containing attachments, URLs, or stickers are left untouched.

### Configuration

Managed via `/config media` (requires MANAGE_GUILD):

- **`add #channel [redirect:#channel] [expires:duration]`** — Add a channel to media-only enforcement. Optionally set a per-channel redirect hint and/or an auto-expiry duration for old messages.
- **`remove #channel`** — Stop monitoring a channel.
- **`status`** — Embed listing all configured channels with redirect and expiry info.

State is persisted to `state/media_channels_{guild_id}.yaml`.

### Media Detection

A message is considered to have media if it contains:
- Any attachment (images, videos, files)
- A URL matching `https?://`
- Any sticker

Requires the `MESSAGE_CONTENT` privileged intent (enabled in Discord Developer Portal under Bot > Privileged Gateway Intents > Message Content Intent).

### Enforcement Flow

1. Non-bot message arrives in a monitored channel.
2. If it has media → leave it alone.
3. If it's text-only → delete it.
4. Post a playful dragon notice mentioning the user (with optional redirect hint).
5. Auto-delete the notice after 15 seconds.
6. Log the action to the guild's log channel via `gc.log()`.

### Notice Copy

```
*chomps happily* 🐉 Mmm, snacks! @user, this channel is for images, links,
and files only — so I had to nom that message right up. Why not share your
thoughts in #redirect? 🐾
```
(Redirect hint omitted if not configured.)

### Hourly Cleanup Cron

Runs at `:30` past each hour. For each media channel with `expiry_minutes` set, calls `ChannelContext.purge_old_messages()` to bulk- or single-delete messages older than the configured duration. Per-guild error isolation.

### File Structure

- **`__init__.py`** — Extension entry point, message event listener
- **`cron.py`** — Hourly media channel cleanup cron task
- **`models.py`** — Pydantic models: `MediaChannelEntry`, `MediaGuildState`
- **`state.py`** — YAML state persistence (load/save with in-memory cache)

### Required Discord Permissions

- `MANAGE_MESSAGES` — to delete user messages and the bot's own notices
- `SEND_MESSAGES` — to post the enforcement notice
- `MESSAGE_CONTENT` intent — to read message content for media detection

### Logging

- **Info**: Media channel added/removed (config commands), old messages purged
- **Warning**: Missing MANAGE_MESSAGES permission, cannot send notice
- **Debug**: Cleanup cron ticks
