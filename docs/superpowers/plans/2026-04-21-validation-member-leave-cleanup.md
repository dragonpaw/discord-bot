# Validation Member Leave Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up state and validate channels when a member leaves mid-onboarding, and reconcile stale entries on bot startup.

**Architecture:** Two new event listeners in `commands.py` — one reacts to live member leaves using the existing `_close_validate_channel` helper (30s delay); one runs at startup via a testable `_reconcile_guild()` helper that checks each persisted member/channel against live Discord state. A small `all_guild_ids()` helper in `state.py` enables the startup scan.

**Tech Stack:** hikari `MemberDeleteEvent` + `StartedEvent`, hikari REST (`fetch_member`, `fetch_channel`), existing `_close_validate_channel` helper, structlog, pytest with `asyncio_mode = "auto"`.

---

## File Map

| File | Change |
|------|--------|
| `dragonpaw_bot/plugins/validation/state.py` | Add `all_guild_ids()` |
| `dragonpaw_bot/plugins/validation/commands.py` | Add `on_member_leave`, `_reconcile_guild`, `on_startup_reconcile` |
| `dragonpaw_bot/plugins/validation/CLAUDE.md` | Document new behaviour |
| `tests/test_validation.py` | Tests for `all_guild_ids` and `_reconcile_guild` |

---

### Task 1: Add `all_guild_ids()` to `state.py`

**Files:**
- Modify: `dragonpaw_bot/plugins/validation/state.py`
- Test: `tests/test_validation.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_validation.py`:

```python
def test_all_guild_ids_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    assert validation_state.all_guild_ids() == []


def test_all_guild_ids_finds_state_files(tmp_path, monkeypatch):
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    (tmp_path / "validation_111.yaml").touch()
    (tmp_path / "validation_222.yaml").touch()
    (tmp_path / "other_333.yaml").touch()  # not a validation file — must be excluded
    assert sorted(validation_state.all_guild_ids()) == [111, 222]
```

- [ ] **Step 2: Run to verify they fail**

```
uv run pytest tests/test_validation.py::test_all_guild_ids_empty tests/test_validation.py::test_all_guild_ids_finds_state_files -v
```

Expected: `FAILED` — `AttributeError: module has no attribute 'all_guild_ids'`

- [ ] **Step 3: Implement `all_guild_ids()` in `state.py`**

Add after the `save()` function:

```python
def all_guild_ids() -> list[int]:
    """Return all guild IDs that have persisted validation state on disk."""
    return [
        int(p.stem.removeprefix("validation_"))
        for p in STATE_DIR.glob("validation_*.yaml")
    ]
```

- [ ] **Step 4: Run to verify they pass**

```
uv run pytest tests/test_validation.py::test_all_guild_ids_empty tests/test_validation.py::test_all_guild_ids_finds_state_files -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add dragonpaw_bot/plugins/validation/state.py tests/test_validation.py
git commit -m "feat(validation): add all_guild_ids() to enumerate persisted state files"
```

---

### Task 2: Add `on_member_leave` listener

**Files:**
- Modify: `dragonpaw_bot/plugins/validation/commands.py`

- [ ] **Step 1: Add the listener**

In `commands.py`, after the `on_message_create` listener and before the `# Interaction handlers` section:

```python
@loader.listener(hikari.MemberDeleteEvent)
async def on_member_leave(event: hikari.MemberDeleteEvent) -> None:
    """Clean up state and validate channel when a member leaves mid-onboarding."""
    bot: DragonpawBot = event.app  # type: ignore[assignment]
    st = validation_state.load(int(event.guild_id))

    member_entry = next(
        (m for m in st.members if m.user_id == int(event.user_id)), None
    )
    if not member_entry:
        return

    st.members = [m for m in st.members if m.user_id != int(event.user_id)]
    validation_state.save(st)

    gc = GuildContext.from_guild(
        bot,
        bot.cache.get_guild(event.guild_id)
        or await bot.rest.fetch_guild(event.guild_id),
    )
    display = (
        event.old_member.display_name if event.old_member else str(int(event.user_id))
    )
    await gc.log(
        f"*sad snort* <@{event.user_id}> flew away before finishing onboarding — "
        f"cleaning up! 🐉"
    )
    logger.info("Removed member from onboarding on leave", user=display)

    if member_entry.channel_id:
        asyncio.get_running_loop().create_task(
            _close_validate_channel(
                gc,
                member_entry.channel_id,
                f"*sad snort* Looks like they flew away before finishing! "
                f"This channel will be deleted in {CHANNEL_CLOSE_DELAY} seconds~ 🐉",
            )
        )
```

- [ ] **Step 2: Run linter and type checker**

```
uv run ruff check dragonpaw_bot/plugins/validation/commands.py
uv run ty check dragonpaw_bot/
```

Expected: no errors.

- [ ] **Step 3: Run the full test suite**

```
uv run pytest tests/test_validation.py -v
```

Expected: all existing tests `PASSED`, no new failures.

- [ ] **Step 4: Commit**

```bash
git add dragonpaw_bot/plugins/validation/commands.py
git commit -m "feat(validation): clean up state and channel when member leaves mid-onboarding"
```

---

### Task 3: Add startup reconciliation

**Files:**
- Modify: `dragonpaw_bot/plugins/validation/commands.py`
- Test: `tests/test_validation.py`

- [ ] **Step 1: Write the failing tests**

Add `import asyncio` to the imports at the top of `tests/test_validation.py` (alongside the existing imports).

Then add these tests to `tests/test_validation.py`:

```python
# ---------------------------------------------------------------------------- #
#                           _reconcile_guild                                    #
# ---------------------------------------------------------------------------- #


def _make_reconcile_bot(*, fetch_member_raises=None, fetch_channel_raises=None):
    """Minimal bot mock for _reconcile_guild tests.

    bot.state() returns None so GuildContext sets log_channel_id=None,
    making gc.log() a silent no-op — no REST create_message calls needed.
    """
    bot = Mock()
    bot.cache = Mock()
    bot.cache.get_guild = Mock(return_value=None)
    bot.state = Mock(return_value=None)

    guild = Mock()
    guild.id = hikari.Snowflake(1)
    guild.name = "Test Guild"

    async def _fetch_guild(*_a, **_kw):
        return guild

    async def _fetch_member(*_a, **_kw):
        if fetch_member_raises:
            raise fetch_member_raises
        return Mock()

    async def _fetch_channel(*_a, **_kw):
        if fetch_channel_raises:
            raise fetch_channel_raises
        return Mock()

    bot.rest = Mock()
    bot.rest.fetch_guild = _fetch_guild
    bot.rest.fetch_member = _fetch_member
    bot.rest.fetch_channel = _fetch_channel
    return bot


async def test_reconcile_guild_no_members(tmp_path, monkeypatch):
    """Skip guilds with no members — no REST calls made."""
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    st = ValidationGuildState(guild_id=1, guild_name="Test")
    validation_state.save(st)

    bot = _make_reconcile_bot()

    from dragonpaw_bot.plugins.validation.commands import _reconcile_guild

    await _reconcile_guild(bot, 1)

    bot.rest.fetch_member.assert_not_called()


async def test_reconcile_guild_member_present_channel_exists(tmp_path, monkeypatch):
    """Member still in guild and channel still exists — no state changes."""
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="Test",
        members=[ValidationMember(user_id=10, joined_at=now, channel_id=99)],
    )
    validation_state.save(st)

    bot = _make_reconcile_bot()

    from dragonpaw_bot.plugins.validation.commands import _reconcile_guild

    await _reconcile_guild(bot, 1)

    validation_state._cache.clear()
    loaded = validation_state.load(1)
    assert len(loaded.members) == 1


async def test_reconcile_guild_member_left(tmp_path, monkeypatch):
    """Member left while bot was offline — removed from state, channel closed."""
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="Test",
        members=[ValidationMember(user_id=10, joined_at=now, channel_id=99)],
    )
    validation_state.save(st)

    bot = _make_reconcile_bot(fetch_member_raises=hikari.NotFoundError("", {}, b""))
    close_calls: list[int] = []

    async def _fake_close(_gc, channel_id, _notice):
        close_calls.append(channel_id)

    monkeypatch.setattr(
        "dragonpaw_bot.plugins.validation.commands._close_validate_channel",
        _fake_close,
    )

    from dragonpaw_bot.plugins.validation.commands import _reconcile_guild

    await _reconcile_guild(bot, 1)
    await asyncio.sleep(0)  # let the create_task coroutine run

    validation_state._cache.clear()
    loaded = validation_state.load(1)
    assert loaded.members == []
    assert close_calls == [99]


async def test_reconcile_guild_channel_deleted(tmp_path, monkeypatch):
    """Member present but validate channel was deleted — removed from state."""
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="Test",
        members=[ValidationMember(user_id=10, joined_at=now, channel_id=99)],
    )
    validation_state.save(st)

    bot = _make_reconcile_bot(fetch_channel_raises=hikari.NotFoundError("", {}, b""))

    from dragonpaw_bot.plugins.validation.commands import _reconcile_guild

    await _reconcile_guild(bot, 1)

    validation_state._cache.clear()
    loaded = validation_state.load(1)
    assert loaded.members == []


async def test_reconcile_guild_no_channel_id_skips_channel_check(tmp_path, monkeypatch):
    """Member still at AWAITING_RULES (no channel yet) and present — no channel fetch."""
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="Test",
        members=[ValidationMember(user_id=10, joined_at=now)],  # channel_id=None
    )
    validation_state.save(st)

    bot = _make_reconcile_bot()

    from dragonpaw_bot.plugins.validation.commands import _reconcile_guild

    await _reconcile_guild(bot, 1)

    bot.rest.fetch_channel.assert_not_called()
    validation_state._cache.clear()
    loaded = validation_state.load(1)
    assert len(loaded.members) == 1
```

- [ ] **Step 2: Run to verify they fail**

```
uv run pytest tests/test_validation.py::test_reconcile_guild_no_members tests/test_validation.py::test_reconcile_guild_member_present_channel_exists tests/test_validation.py::test_reconcile_guild_member_left tests/test_validation.py::test_reconcile_guild_channel_deleted tests/test_validation.py::test_reconcile_guild_no_channel_id_skips_channel_check -v
```

Expected: `FAILED` — `ImportError: cannot import name '_reconcile_guild'`

- [ ] **Step 3: Implement `_reconcile_guild` and `on_startup_reconcile` in `commands.py`**

Add after `on_member_leave` and before the `# Interaction handlers` section:

```python
async def _reconcile_guild(bot: DragonpawBot, guild_id: int) -> None:
    """Check all in-progress validations for one guild and remove stale entries."""
    st = validation_state.load(guild_id)
    if not st.members:
        return

    gc = GuildContext.from_guild(
        bot,
        bot.cache.get_guild(guild_id) or await bot.rest.fetch_guild(guild_id),
    )

    to_remove: list[int] = []

    for member_entry in st.members:
        try:
            await bot.rest.fetch_member(guild_id, member_entry.user_id)
        except hikari.NotFoundError:
            to_remove.append(member_entry.user_id)
            await gc.log(
                f"*sad snort* <@{member_entry.user_id}> left the server while I was napping — "
                f"cleaning up their onboarding! 🐉"
            )
            logger.info(
                "Startup reconcile: member left while offline",
                user_id=member_entry.user_id,
            )
            if member_entry.channel_id:
                asyncio.get_running_loop().create_task(
                    _close_validate_channel(
                        gc,
                        member_entry.channel_id,
                        f"*sad snort* Looks like they flew away while I was napping! "
                        f"This channel will be deleted in {CHANNEL_CLOSE_DELAY} seconds~ 🐉",
                    )
                )
            continue
        except hikari.HTTPError:
            logger.warning(
                "Startup reconcile: failed to fetch member",
                user_id=member_entry.user_id,
                guild_id=guild_id,
            )
            continue

        if not member_entry.channel_id:
            continue

        try:
            await bot.rest.fetch_channel(member_entry.channel_id)
        except hikari.NotFoundError:
            to_remove.append(member_entry.user_id)
            await gc.log(
                f"*confused sniff* Validate channel for <@{member_entry.user_id}> is gone — "
                f"cleaned up their onboarding entry! 🐉"
            )
            logger.info(
                "Startup reconcile: validate channel gone",
                user_id=member_entry.user_id,
                channel_id=member_entry.channel_id,
            )
        except hikari.HTTPError:
            logger.warning(
                "Startup reconcile: failed to fetch channel",
                user_id=member_entry.user_id,
                channel_id=member_entry.channel_id,
                guild_id=guild_id,
            )

    if to_remove:
        remove_ids = set(to_remove)
        st.members = [m for m in st.members if m.user_id not in remove_ids]
        validation_state.save(st)


@loader.listener(hikari.StartedEvent)
async def on_startup_reconcile(event: hikari.StartedEvent) -> None:
    """On startup, clean up validation state for members who left or had channels deleted."""
    bot: DragonpawBot = event.app  # type: ignore[assignment]
    logger.info("Running startup validation reconcile")
    for guild_id in validation_state.all_guild_ids():
        try:
            await _reconcile_guild(bot, guild_id)
        except Exception:
            logger.exception("Startup reconcile failed for guild", guild_id=guild_id)
```

- [ ] **Step 4: Run to verify the new tests pass**

```
uv run pytest tests/test_validation.py::test_reconcile_guild_no_members tests/test_validation.py::test_reconcile_guild_member_present_channel_exists tests/test_validation.py::test_reconcile_guild_member_left tests/test_validation.py::test_reconcile_guild_channel_deleted tests/test_validation.py::test_reconcile_guild_no_channel_id_skips_channel_check -v
```

Expected: all `PASSED`

- [ ] **Step 5: Run the full test suite**

```
uv run pytest -v
```

Expected: all tests `PASSED`.

- [ ] **Step 6: Run linter and type checker**

```
uv run ruff check dragonpaw_bot/
uv run ty check dragonpaw_bot/
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add dragonpaw_bot/plugins/validation/commands.py tests/test_validation.py
git commit -m "feat(validation): reconcile stale onboarding entries on startup"
```

---

### Task 4: Update CLAUDE.md

**Files:**
- Modify: `dragonpaw_bot/plugins/validation/CLAUDE.md`

- [ ] **Step 1: Replace the Rejection section with the expanded version**

Replace the existing `### Rejection` section (lines starting with `### Rejection` through the blank line before `### Configuration`) with:

```markdown
### Member Leave Cleanup

If a member leaves the server mid-onboarding, `on_member_leave` (`MemberDeleteEvent`)
removes them from state and fires `_close_validate_channel` with a 30-second delay so
staff see a goodbye notice before the channel disappears. `event.old_member` may be
`None` (cache miss) — user ID is always used for the Discord mention.

### Startup Reconciliation

`on_startup_reconcile` (`StartedEvent`) iterates all guilds with persisted state via
`all_guild_ids()` and calls `_reconcile_guild()` for each. For every member entry:

- **Member left while offline** — REST 404 on `fetch_member` → remove from state, fire
  `_close_validate_channel` (30-second delay) if a channel exists.
- **Channel deleted while offline** — member present but REST 404 on `fetch_channel` →
  remove from state (no channel to delete).
- HTTP errors other than 404 → log a warning and skip that entry.

Each guild is wrapped in its own `try/except` so one failure doesn't abort others.

### Rejection

There is no reject button. Staff close the validate channel manually (deleting the channel
removes the member from Discord's view; bot state is cleaned up on the next
`MemberDeleteEvent` or `StartedEvent` reconcile).
```

- [ ] **Step 2: Update the File Structure section**

In the `### File Structure` bullet for `commands.py`, update the description to list the new additions:

```markdown
- **`commands.py`** — All event listeners (`on_member_join`, `on_member_leave`,
  `on_startup_reconcile`, `on_member_update`, `on_message_create`), cron task,
  interaction/modal handlers, and helpers (`_close_validate_channel`, `_reconcile_guild`,
  `_sanitize_channel_name`, `_is_staff`)
- **`state.py`** — YAML state persistence (load/save with in-memory cache).
  `all_guild_ids()` returns all guild IDs with persisted state files on disk.
```

- [ ] **Step 3: Commit**

```bash
git add dragonpaw_bot/plugins/validation/CLAUDE.md
git commit -m "docs(validation): document member-leave cleanup and startup reconciliation"
```
