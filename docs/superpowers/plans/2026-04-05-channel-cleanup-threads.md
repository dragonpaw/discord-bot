# Channel Cleanup — Thread Auto-Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-delete stale threads from cleanup channels alongside existing message cleanup.

**Architecture:** Add `purge_old_threads(expiry_minutes)` to `ChannelContext`, called from the existing `run_cleanup` after `purge_old_messages`. Thread activity is measured by `last_message_id.created_at` (falling back to `thread.created_at` when no messages). Add `MANAGE_THREADS` to `CHANNEL_CLEANUP_PERMS` so the existing proactive permission check covers it.

**Tech Stack:** hikari (`fetch_active_threads`, `fetch_public_archived_threads`, `delete_channel`), pytest-asyncio

---

### Task 1: Write failing tests for `purge_old_threads`

**Files:**
- Modify: `tests/test_purge_old_messages.py`

- [ ] **Step 1: Add thread helper and test cases to the test file**

Append to `tests/test_purge_old_messages.py`:

```python
# ---------------------------------------------------------------------------- #
#                            purge_old_threads tests                           #
# ---------------------------------------------------------------------------- #


def _thread(
    age_hours: float,
    *,
    has_messages: bool = True,
    channel_id: int = CHANNEL_ID,
) -> Mock:
    """Create a mock GuildPublicThread."""
    thread = Mock(spec=hikari.GuildPublicThread)
    thread.id = hikari.Snowflake(int(age_hours * 10000 + 100_000))
    thread.name = f"thread-{age_hours}h"
    thread.parent_id = hikari.Snowflake(channel_id)
    thread.created_at = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    if has_messages:
        last_msg = Mock()
        last_msg.created_at = datetime.now(timezone.utc) - timedelta(hours=age_hours)
        thread.last_message_id = last_msg
    else:
        thread.last_message_id = None
    return thread


def _make_cc_threads(
    active: list[Mock] | None = None,
    archived: list[Mock] | None = None,
    *,
    fetch_active_side_effect: Exception | None = None,
    fetch_archived_side_effect: Exception | None = None,
) -> ChannelContext:
    bot = Mock()

    if fetch_active_side_effect:
        bot.rest.fetch_active_threads = AsyncMock(side_effect=fetch_active_side_effect)
    else:
        bot.rest.fetch_active_threads = AsyncMock(return_value=active or [])

    if fetch_archived_side_effect:
        bot.rest.fetch_public_archived_threads = Mock(
            side_effect=fetch_archived_side_effect
        )
    else:
        _archived = archived or []

        async def _archived_gen(*args, **kwargs):
            for t in _archived:
                yield t

        bot.rest.fetch_public_archived_threads = Mock(side_effect=_archived_gen)

    bot.rest.delete_channel = AsyncMock()
    bot.rest.create_message = AsyncMock()

    return ChannelContext(
        bot=bot,
        guild_id=hikari.Snowflake(1),
        name=GUILD,
        log_channel_id=None,
        channel_id=hikari.Snowflake(CHANNEL_ID),
        channel_name=CHANNEL,
    )


async def test_purge_old_threads_stale_active_thread_deleted():
    t = _thread(age_hours=48)  # 48h old, expiry 1h → delete
    cc = _make_cc_threads(active=[t])
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 1
    cc.bot.rest.delete_channel.assert_called_once_with(t.id)


async def test_purge_old_threads_fresh_active_thread_kept():
    t = _thread(age_hours=0.5)  # 30m old, expiry 2h → keep
    cc = _make_cc_threads(active=[t])
    count = await cc.purge_old_threads(expiry_minutes=120)
    assert count == 0
    cc.bot.rest.delete_channel.assert_not_called()


async def test_purge_old_threads_stale_archived_thread_deleted():
    t = _thread(age_hours=72)  # archived thread, 72h old, expiry 1h
    cc = _make_cc_threads(archived=[t])
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 1
    cc.bot.rest.delete_channel.assert_called_once_with(t.id)


async def test_purge_old_threads_skips_threads_from_other_channels():
    t = _thread(age_hours=48, channel_id=999)  # different parent
    cc = _make_cc_threads(active=[t])
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 0
    cc.bot.rest.delete_channel.assert_not_called()


async def test_purge_old_threads_no_messages_uses_created_at():
    # Thread has no messages; created 48h ago; expiry 1h → stale
    t = _thread(age_hours=48, has_messages=False)
    cc = _make_cc_threads(active=[t])
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 1
    cc.bot.rest.delete_channel.assert_called_once_with(t.id)


async def test_purge_old_threads_not_found_on_delete_swallowed():
    t = _thread(age_hours=48)
    cc = _make_cc_threads(active=[t])
    cc.bot.rest.delete_channel = AsyncMock(
        side_effect=hikari.NotFoundError(url="x", headers={}, raw_body=b"")
    )
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 1  # Still counted as deleted (was already gone)


async def test_purge_old_threads_forbidden_on_delete_breaks_loop_and_logs():
    threads = [_thread(age_hours=48 + i) for i in range(3)]
    cc = _make_cc_threads(active=threads, archived=[])
    cc.bot.rest.delete_channel = AsyncMock(
        side_effect=hikari.ForbiddenError(url="x", headers={}, raw_body=b"")
    )
    cc.log_channel_id = hikari.Snowflake(99)
    count = await cc.purge_old_threads(expiry_minutes=60)
    # Stops after first failure
    assert cc.bot.rest.delete_channel.call_count == 1
    # Posts to log channel
    cc.bot.rest.create_message.assert_called_once()
    content = cc.bot.rest.create_message.call_args.kwargs["content"]
    assert "Manage Threads" in content


async def test_purge_old_threads_forbidden_on_fetch_active_returns_zero():
    cc = _make_cc_threads(
        fetch_active_side_effect=hikari.ForbiddenError(url="x", headers={}, raw_body=b"")
    )
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 0
    cc.bot.rest.delete_channel.assert_not_called()


async def test_purge_old_threads_mixed_active_and_archived():
    active_stale = _thread(age_hours=48)
    archived_stale = _thread(age_hours=96)
    fresh = _thread(age_hours=0.5)
    cc = _make_cc_threads(active=[active_stale, fresh], archived=[archived_stale])
    count = await cc.purge_old_threads(expiry_minutes=60)
    assert count == 2
    assert cc.bot.rest.delete_channel.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_purge_old_messages.py -k "purge_old_threads" -v
```

Expected: `AttributeError: 'ChannelContext' object has no attribute 'purge_old_threads'`

---

### Task 2: Add `MANAGE_THREADS` to permissions and implement `purge_old_threads`

**Files:**
- Modify: `dragonpaw_bot/context.py:578-582` (CHANNEL_CLEANUP_PERMS)
- Modify: `dragonpaw_bot/context.py:474` (insert new method after `purge_old_messages`)

- [ ] **Step 1: Add `MANAGE_THREADS` to `CHANNEL_CLEANUP_PERMS`**

In `dragonpaw_bot/context.py`, update the dict:

```python
CHANNEL_CLEANUP_PERMS: dict[hikari.Permissions, str] = {
    hikari.Permissions.VIEW_CHANNEL: "View Channel",
    hikari.Permissions.READ_MESSAGE_HISTORY: "Read Message History",
    hikari.Permissions.MANAGE_MESSAGES: "Manage Messages",
    hikari.Permissions.MANAGE_THREADS: "Manage Threads",
}
```

- [ ] **Step 2: Add `purge_old_threads` method to `ChannelContext`**

Insert after the closing of `purge_old_messages` (after line 474) and before `delete_my_messages`:

```python
async def purge_old_threads(self, expiry_minutes: int) -> int:
    """Delete threads in this channel whose last activity is older than expiry_minutes.

    Checks both active threads (filtered to this channel) and archived public threads.
    Last activity is taken from last_message_id.created_at, falling back to thread
    creation time when no messages exist. Returns count of deleted threads.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=expiry_minutes)
    threads: list[hikari.GuildThreadChannel] = []

    try:
        active = await self.bot.rest.fetch_active_threads(self.guild_id)
        threads.extend(t for t in active if t.parent_id == self.channel_id)
    except (hikari.ForbiddenError, hikari.NotFoundError) as exc:
        self.logger.warning(
            "Cannot fetch active threads for cleanup",
            channel=self.channel_name,
            error=str(exc),
        )
        return 0

    try:
        async for thread in self.bot.rest.fetch_public_archived_threads(self.channel_id):
            threads.append(thread)
    except (hikari.ForbiddenError, hikari.NotFoundError) as exc:
        self.logger.warning(
            "Cannot fetch archived threads for cleanup",
            channel=self.channel_name,
            error=str(exc),
        )

    deleted = 0
    for thread in threads:
        last_activity = (
            thread.last_message_id.created_at
            if thread.last_message_id is not None
            else thread.created_at
        )
        if last_activity >= cutoff:
            continue
        try:
            await self.bot.rest.delete_channel(thread.id)
            deleted += 1
        except hikari.NotFoundError:
            deleted += 1  # Already gone — count it as done
        except hikari.ForbiddenError as exc:
            self.logger.warning(
                "Cannot delete thread, stopping",
                channel=self.channel_name,
                thread=thread.name,
                error=str(exc),
            )
            await self.log(
                f"⚠️ I can't delete threads in **#{self.channel_name}**. "
                f"Please grant me **Manage Threads** permission in that channel."
            )
            break

    if deleted:
        self.logger.info("Purged old threads", channel=self.channel_name, count=deleted)

    return deleted
```

- [ ] **Step 3: Run the new tests**

```bash
uv run pytest tests/test_purge_old_messages.py -k "purge_old_threads" -v
```

Expected: all 9 thread tests PASS

- [ ] **Step 4: Run the full test suite to check nothing regressed**

```bash
uv run pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add dragonpaw_bot/context.py tests/test_purge_old_messages.py
git commit -m "feat(channel_cleanup): add purge_old_threads to ChannelContext"
```

---

### Task 3: Call `purge_old_threads` from `run_cleanup`

**Files:**
- Modify: `dragonpaw_bot/context.py:487-520` (`run_cleanup` method)

- [ ] **Step 1: Write failing test for `run_cleanup` calling thread cleanup**

Append to `tests/test_purge_old_messages.py`:

```python
# ---------------------------------------------------------------------------- #
#                    run_cleanup calls purge_old_threads                       #
# ---------------------------------------------------------------------------- #


async def test_run_cleanup_calls_purge_old_threads(monkeypatch):
    """run_cleanup should purge threads as well as messages."""
    cc = _make_cc_threads()
    monkeypatch.setattr(cc, "check_perms", AsyncMock(return_value=[]))
    mock_msgs = AsyncMock(return_value=0)
    mock_threads = AsyncMock(return_value=0)
    monkeypatch.setattr(cc, "purge_old_messages", mock_msgs)
    monkeypatch.setattr(cc, "purge_old_threads", mock_threads)

    await cc.run_cleanup(expiry_minutes=60)

    mock_msgs.assert_awaited_once_with(60)
    mock_threads.assert_awaited_once_with(60)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_purge_old_messages.py::test_run_cleanup_calls_purge_old_threads -v
```

Expected: FAIL (`purge_old_threads` not called)

- [ ] **Step 3: Update `run_cleanup` to call `purge_old_threads`**

In `dragonpaw_bot/context.py`, replace the `run_cleanup` method body:

```python
async def run_cleanup(self, expiry_minutes: int) -> None:
    """Check permissions then purge old messages and threads, logging any issues.

    Combines the proactive permission check with purge_old_messages and
    purge_old_threads with error handling. Use this from cron tasks instead
    of calling purge methods directly.
    """
    missing = await self.check_perms(CHANNEL_CLEANUP_PERMS)
    if missing:
        self.logger.warning(
            "Missing permissions for cleanup, skipping",
            channel=self.channel_name,
            missing=missing,
        )
        await self.log(
            f"⚠️ I'm missing **{', '.join(missing)}** in **#{self.channel_name}** "
            f"and can't run cleanup. Please fix the channel permissions."
        )
        return
    try:
        deleted = await self.purge_old_messages(expiry_minutes)
        if deleted:
            self.logger.info(
                "Purged old messages",
                channel=self.channel_name,
                count=deleted,
            )
    except Exception:
        self.logger.exception(
            "Cleanup cron error",
            channel=self.channel_name,
        )
        await self.log(
            f"🐛 I hit an unexpected error cleaning **#{self.channel_name}** — check the bot logs."
        )
    try:
        await self.purge_old_threads(expiry_minutes)
    except Exception:
        self.logger.exception(
            "Thread cleanup cron error",
            channel=self.channel_name,
        )
        await self.log(
            f"🐛 I hit an unexpected error cleaning threads in **#{self.channel_name}** — check the bot logs."
        )
```

- [ ] **Step 4: Run the full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add dragonpaw_bot/context.py tests/test_purge_old_messages.py
git commit -m "feat(channel_cleanup): run_cleanup now purges stale threads"
```

---

### Task 4: Update CLAUDE.md

**Files:**
- Modify: `dragonpaw_bot/plugins/channel_cleanup/CLAUDE.md`

- [ ] **Step 1: Update the plugin CLAUDE.md**

Replace the `CLAUDE.md` content to document thread cleanup:

```markdown
## Channel Cleanup Plugin

Auto-deletes messages **and threads** older than a configured duration from any channel. Runs hourly as a background cron task with no user-facing commands (configuration is handled by the `config` plugin via `/config cleanup`).

### Configuration

Managed via `/config cleanup` (guild owner only):

- **`add #channel expires:duration`** — Add a channel for auto-expiry.
- **`remove #channel`** — Stop monitoring a channel.
- **`status`** — Embed listing all configured channels with expiry info.

State is persisted to `state/channel_cleanup_{guild_id}.yaml`.

### Hourly Cleanup Cron

Runs at the top of each hour (`0 * * * *`). For each configured channel, first checks bot permissions via `cc.check_perms(CHANNEL_CLEANUP_PERMS)` — if any are missing, logs a warning and posts to the guild log channel, then skips that channel. Otherwise:

1. Calls `ChannelContext.purge_old_messages()` to delete messages older than the configured duration.
2. Calls `ChannelContext.purge_old_threads()` to delete threads whose last activity (last message, or creation time if no messages) is older than the configured duration.

`purge_old_messages` uses bulk delete (up to 100 messages per call) for messages younger than 14 days, and single deletes for older messages (Discord limitation). `purge_old_threads` fetches both active threads (guild-wide, filtered by parent channel) and archived public threads, then deletes stale ones via `rest.delete_channel`.

Per-guild error isolation — one guild's failure doesn't abort others. Message and thread cleanup are also isolated from each other — a thread error doesn't abort message cleanup.

Note: `fetch_messages` silently returns empty results (rather than raising) when the bot lacks `READ_MESSAGE_HISTORY`, so the permission check must be proactive rather than reactive.

### File Structure

- **`__init__.py`** — Extension entry point
- **`cron.py`** — Hourly cleanup cron task
- **`models.py`** — Pydantic models: `CleanupChannelEntry`, `CleanupGuildState`
- **`state.py`** — YAML state persistence (load/save with in-memory cache)

### Required Discord Permissions

- `VIEW_CHANNEL` — to see the channel
- `MANAGE_MESSAGES` — to delete messages
- `READ_MESSAGE_HISTORY` — to fetch old messages
- `MANAGE_THREADS` — to delete threads

### Logging

- **Info**: Old messages purged (count logged); old threads purged (count logged)
- **Debug**: Cron tick, cleanup progress for large single-delete batches
- **Warning**: Missing channel permissions detected at cron time — structlog warning + guild log message with instructions to fix; thread fetch/delete failures
- **Warning/Exception**: Unexpected cron errors — structlog exception + guild log message
```

- [ ] **Step 2: Run linting and type check**

```bash
uv run ruff check dragonpaw_bot/ && uv run ty check dragonpaw_bot/
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add dragonpaw_bot/plugins/channel_cleanup/CLAUDE.md
git commit -m "docs(channel_cleanup): document thread cleanup in CLAUDE.md"
```
