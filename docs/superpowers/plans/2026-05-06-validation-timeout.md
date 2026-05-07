# Validation Timeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the count-based `max_reminders` kick system with a hard 7-day timeout from join, 18h reminder pings in the appropriate channel, and automatic kick + channel close on expiry.

**Architecture:** Extend the hourly cron in `cron.py` to sweep all non-`AWAITING_STAFF` members, checking the 7-day deadline first and 18h reminder cadence second. Remove `max_reminders` from the data model and config command entirely; two module-level constants take its place.

**Tech Stack:** Python 3.13, hikari, hikari-lightbulb v3, Pydantic v2, pytest-asyncio (asyncio_mode=auto)

---

## Files Modified

- `dragonpaw_bot/plugins/validation/models.py` — remove `max_reminders` field
- `dragonpaw_bot/plugins/validation/cron.py` — rewrite reminder/kick logic
- `dragonpaw_bot/plugins/validation/config.py` — remove `max_reminders` option and status line
- `dragonpaw_bot/plugins/validation/CLAUDE.md` — update docs
- `tests/test_validation.py` — remove `max_reminders` tests, add cron tests

---

### Task 1: Update model tests to drop max_reminders references

**Files:**
- Modify: `tests/test_validation.py`

- [ ] **Step 1: Edit `test_validation_guild_state_defaults`** — remove the `max_reminders` assertion

```python
def test_validation_guild_state_defaults():
    st = ValidationGuildState(guild_id=100, guild_name="Test")
    assert st.lobby_channel_id is None
    assert st.member_role_id is None
    assert st.staff_role_id is None
    assert st.members == []
```

- [ ] **Step 2: Delete `test_validation_guild_state_max_reminders_min_1`** — this test validates a field we are removing; delete the entire function.

- [ ] **Step 3: Edit `test_validation_guild_state_round_trip`** — remove `max_reminders=5` from the constructor and its assertion

Replace the `st = ValidationGuildState(...)` block with:

```python
st = ValidationGuildState(
    guild_id=100,
    guild_name="Test Guild",
    lobby_channel_id=200,
    member_role_id=300,
    staff_role_id=400,
    members=[member],
)
```

And remove:
```python
assert loaded.max_reminders == 5
```

- [ ] **Step 4: Run existing tests to confirm they still pass**

```bash
uv run pytest tests/test_validation.py -v
```

Expected: all tests PASS (field still exists, but no test references it)

- [ ] **Step 5: Commit**

```bash
git add tests/test_validation.py
git commit -m "test(validation): drop max_reminders test references before field removal"
```

---

### Task 2: Remove max_reminders from ValidationGuildState

**Files:**
- Modify: `dragonpaw_bot/plugins/validation/models.py`

- [ ] **Step 1: Remove the `max_reminders` field** from `ValidationGuildState`

```python
class ValidationGuildState(pydantic.BaseModel):
    guild_id: int
    guild_name: str
    # config
    lobby_channel_id: int | None = None
    validate_category_id: int | None = None
    member_role_id: int | None = None
    staff_role_id: int | None = None
    # welcome message channel links
    about_channel_id: int | None = None
    roles_channel_id: int | None = None
    intros_channel_id: int | None = None
    events_channel_id: int | None = None
    chat_channel_id: int | None = None
    # runtime
    members: list[ValidationMember] = []
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/test_validation.py -v
```

Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add dragonpaw_bot/plugins/validation/models.py
git commit -m "feat(validation): remove max_reminders config field"
```

---

### Task 3: Write failing cron tests

**Files:**
- Modify: `tests/test_validation.py`

- [ ] **Step 1: Add imports at the top of the test file**

Add `timedelta` to the existing datetime import and add `asyncio` if not already imported:

```python
import asyncio
from datetime import UTC, datetime, timedelta
```

- [ ] **Step 2: Add a cron bot factory helper and all cron tests** at the bottom of `tests/test_validation.py`

```python
# ---------------------------------------------------------------------------- #
#                         validation_reminder_cron                              #
# ---------------------------------------------------------------------------- #


def _make_cron_bot(*, guild_id: int = 1, guild_name: str = "TestGuild"):
    """Minimal bot mock for cron tests. bot.state returns None so gc.log() is a no-op."""
    guild = Mock()
    guild.id = hikari.Snowflake(guild_id)
    guild.name = guild_name

    bot = Mock()
    bot.cache = Mock()
    bot.cache.get_guilds_view = Mock(return_value={guild_id: guild})
    bot.state = Mock(return_value=None)
    bot.rest = Mock()
    bot.rest.kick_user = AsyncMock()
    bot.rest.create_message = AsyncMock()
    return bot


async def test_cron_skips_awaiting_staff(tmp_path, monkeypatch):
    """AWAITING_STAFF members are not pinged or kicked, even past the 7-day deadline."""
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="TestGuild",
        lobby_channel_id=10,
        members=[
            ValidationMember(
                user_id=42,
                joined_at=now - timedelta(days=8),
                stage=ValidationStage.AWAITING_STAFF,
                channel_id=55,
            )
        ],
    )
    validation_state.save(st)

    bot = _make_cron_bot()

    from dragonpaw_bot.plugins.validation.cron import validation_reminder_cron

    await validation_reminder_cron(bot)

    bot.rest.kick_user.assert_not_called()
    bot.rest.create_message.assert_not_called()


async def test_cron_no_reminder_before_18h(tmp_path, monkeypatch):
    """Member joined 10h ago — too soon for first reminder; nothing happens."""
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="TestGuild",
        lobby_channel_id=10,
        members=[
            ValidationMember(
                user_id=42,
                joined_at=now - timedelta(hours=10),
                stage=ValidationStage.AWAITING_RULES,
            )
        ],
    )
    validation_state.save(st)

    bot = _make_cron_bot()

    from dragonpaw_bot.plugins.validation.cron import validation_reminder_cron

    await validation_reminder_cron(bot)

    bot.rest.create_message.assert_not_called()
    bot.rest.kick_user.assert_not_called()


async def test_cron_18h_reminder_awaiting_rules(tmp_path, monkeypatch):
    """AWAITING_RULES member 20h after join gets a reminder in the lobby channel."""
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="TestGuild",
        lobby_channel_id=10,
        members=[
            ValidationMember(
                user_id=42,
                joined_at=now - timedelta(hours=20),
                stage=ValidationStage.AWAITING_RULES,
            )
        ],
    )
    validation_state.save(st)

    bot = _make_cron_bot()

    from dragonpaw_bot.plugins.validation.cron import validation_reminder_cron

    await validation_reminder_cron(bot)

    bot.rest.create_message.assert_called_once()
    assert bot.rest.create_message.call_args.kwargs["channel"] == 10

    validation_state._cache.clear()
    assert validation_state.load(1).members[0].reminder_count == 1


async def test_cron_18h_reminder_awaiting_photos(tmp_path, monkeypatch):
    """AWAITING_PHOTOS member 20h after join gets a reminder in their validate channel."""
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="TestGuild",
        lobby_channel_id=10,
        members=[
            ValidationMember(
                user_id=42,
                joined_at=now - timedelta(hours=20),
                stage=ValidationStage.AWAITING_PHOTOS,
                channel_id=55,
            )
        ],
    )
    validation_state.save(st)

    bot = _make_cron_bot()

    from dragonpaw_bot.plugins.validation.cron import validation_reminder_cron

    await validation_reminder_cron(bot)

    bot.rest.create_message.assert_called_once()
    assert bot.rest.create_message.call_args.kwargs["channel"] == 55

    validation_state._cache.clear()
    assert validation_state.load(1).members[0].reminder_count == 1


async def test_cron_deadline_kicks_awaiting_rules(tmp_path, monkeypatch):
    """AWAITING_RULES member past 7 days is kicked and removed from state. No channel close."""
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="TestGuild",
        lobby_channel_id=10,
        members=[
            ValidationMember(
                user_id=42,
                joined_at=now - timedelta(days=8),
                stage=ValidationStage.AWAITING_RULES,
            )
        ],
    )
    validation_state.save(st)

    bot = _make_cron_bot()
    close_calls: list[int] = []

    async def _fake_close(_gc, channel_id, _notice):
        close_calls.append(channel_id)

    monkeypatch.setattr(
        "dragonpaw_bot.plugins.validation.cron._close_validate_channel",
        _fake_close,
    )

    from dragonpaw_bot.plugins.validation.cron import validation_reminder_cron

    await validation_reminder_cron(bot)

    bot.rest.kick_user.assert_called_once()
    assert close_calls == []

    validation_state._cache.clear()
    assert validation_state.load(1).members == []


async def test_cron_deadline_kicks_and_closes_awaiting_photos(tmp_path, monkeypatch):
    """AWAITING_PHOTOS member past 7 days is kicked and validate channel is closed."""
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="TestGuild",
        lobby_channel_id=10,
        members=[
            ValidationMember(
                user_id=42,
                joined_at=now - timedelta(days=8),
                stage=ValidationStage.AWAITING_PHOTOS,
                channel_id=55,
            )
        ],
    )
    validation_state.save(st)

    bot = _make_cron_bot()
    close_calls: list[int] = []

    async def _fake_close(_gc, channel_id, _notice):
        close_calls.append(channel_id)

    monkeypatch.setattr(
        "dragonpaw_bot.plugins.validation.cron._close_validate_channel",
        _fake_close,
    )

    from dragonpaw_bot.plugins.validation.cron import validation_reminder_cron

    await validation_reminder_cron(bot)
    await asyncio.sleep(0)  # let the create_task fire

    bot.rest.kick_user.assert_called_once()
    assert close_calls == [55]

    validation_state._cache.clear()
    assert validation_state.load(1).members == []


async def test_cron_deadline_missing_channel_still_kicks(tmp_path, monkeypatch):
    """AWAITING_PHOTOS past deadline with no channel_id is still kicked; no close attempted."""
    monkeypatch.setattr(validation_state, "STATE_DIR", tmp_path)
    validation_state._cache.clear()

    now = datetime.now(UTC)
    st = ValidationGuildState(
        guild_id=1,
        guild_name="TestGuild",
        lobby_channel_id=10,
        members=[
            ValidationMember(
                user_id=42,
                joined_at=now - timedelta(days=8),
                stage=ValidationStage.AWAITING_PHOTOS,
                channel_id=None,
            )
        ],
    )
    validation_state.save(st)

    bot = _make_cron_bot()
    close_calls: list[int] = []

    async def _fake_close(_gc, channel_id, _notice):
        close_calls.append(channel_id)

    monkeypatch.setattr(
        "dragonpaw_bot.plugins.validation.cron._close_validate_channel",
        _fake_close,
    )

    from dragonpaw_bot.plugins.validation.cron import validation_reminder_cron

    await validation_reminder_cron(bot)

    bot.rest.kick_user.assert_called_once()
    assert close_calls == []

    validation_state._cache.clear()
    assert validation_state.load(1).members == []
```

- [ ] **Step 3: Run the new tests to confirm they FAIL**

```bash
uv run pytest tests/test_validation.py -k "cron" -v
```

Expected: all 7 cron tests FAIL (cron still uses old logic / `_close_validate_channel` not imported there)

---

### Task 4: Rewrite cron.py

**Files:**
- Modify: `dragonpaw_bot/plugins/validation/cron.py`

- [ ] **Step 1: Replace the entire file with the new implementation**

```python
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import hikari
import lightbulb
import structlog

from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.validation import state as validation_state
from dragonpaw_bot.plugins.validation.commands import _close_validate_channel
from dragonpaw_bot.plugins.validation.models import ValidationStage

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)
loader = lightbulb.Loader()

REMINDER_INTERVAL_HOURS = 18
MAX_VALIDATION_DAYS = 7


@loader.task(lightbulb.crontrigger("15 * * * *"))  # every hour
async def validation_reminder_cron(
    bot: hikari.GatewayBot = lightbulb.di.INJECTED,
) -> None:
    """Ping unvalidated members every 18h; kick and close channel after 7 days."""
    bot = cast("DragonpawBot", bot)
    now = datetime.now(UTC)
    guilds = list(bot.cache.get_guilds_view().values())

    for guild in guilds:
        try:
            st = validation_state.load(int(guild.id))
            if not st.lobby_channel_id:
                continue

            gc = GuildContext.from_guild(bot, guild)
            to_remove: list[int] = []
            deadline = timedelta(days=MAX_VALIDATION_DAYS)

            for member in st.members:
                if member.stage == ValidationStage.AWAITING_STAFF:
                    continue

                if now >= member.joined_at + deadline:
                    await gc.kick_member(
                        member.user_id,
                        reason=f"Did not complete validation within {MAX_VALIDATION_DAYS} days",
                    )
                    if member.channel_id:
                        asyncio.create_task(
                            _close_validate_channel(
                                gc,
                                member.channel_id,
                                f"*puffs a small smoke ring* ⏰ Hey <@{member.user_id}> — "
                                f"your {MAX_VALIDATION_DAYS}-day validation window has closed. "
                                f"This channel will disappear shortly. "
                                f"You're welcome to rejoin the server and try again! 🐉",
                            )
                        )
                    to_remove.append(member.user_id)
                    continue

                next_reminder = member.joined_at + timedelta(
                    hours=REMINDER_INTERVAL_HOURS * (member.reminder_count + 1)
                )
                if now < next_reminder:
                    continue

                if member.stage == ValidationStage.AWAITING_RULES:
                    try:
                        await bot.rest.create_message(
                            channel=st.lobby_channel_id,
                            content=(
                                f"*gentle nudge* Hey <@{member.user_id}>! 🐉 Just a little reminder — "
                                f"you haven't finished reading the rules yet! Give 'em a read and "
                                f"click the button in my earlier message when you're ready~ 🐾"
                            ),
                        )
                    except hikari.HTTPError:
                        logger.warning(
                            "Failed to send lobby reminder",
                            user_id=member.user_id,
                            guild=guild.name,
                        )
                    else:
                        member.reminder_count += 1
                        logger.debug(
                            "Sent lobby reminder",
                            user_id=member.user_id,
                            reminder_count=member.reminder_count,
                            guild=guild.name,
                        )
                elif member.stage == ValidationStage.AWAITING_PHOTOS and member.channel_id:
                    try:
                        await bot.rest.create_message(
                            channel=member.channel_id,
                            content=(
                                f"*peers in curiously* Hey <@{member.user_id}>! 🐉 Don't forget — "
                                f"I'm still waiting for your verification photos! Drop at least 2 "
                                f"photos in here when you're ready~ 🐾"
                            ),
                        )
                    except hikari.HTTPError:
                        logger.warning(
                            "Failed to send photo reminder",
                            user_id=member.user_id,
                            guild=guild.name,
                        )
                    else:
                        member.reminder_count += 1
                        logger.debug(
                            "Sent photo reminder",
                            user_id=member.user_id,
                            reminder_count=member.reminder_count,
                            guild=guild.name,
                        )

            if to_remove:
                st.members = [m for m in st.members if m.user_id not in to_remove]

            validation_state.save(st)
        except Exception:
            logger.exception("Error in validation cron for guild", guild=guild.name)
```

- [ ] **Step 2: Run all cron tests**

```bash
uv run pytest tests/test_validation.py -k "cron" -v
```

Expected: all 7 cron tests PASS

- [ ] **Step 3: Run the full test suite**

```bash
uv run pytest tests/test_validation.py -v
```

Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add dragonpaw_bot/plugins/validation/cron.py tests/test_validation.py
git commit -m "feat(validation): 7-day timeout with 18h reminders, kick+close on expiry"
```

---

### Task 5: Update config.py

**Files:**
- Modify: `dragonpaw_bot/plugins/validation/config.py`

- [ ] **Step 1: Remove the `max_reminders` option from `ValidationSetup`**

Delete this class attribute:

```python
    max_reminders = lightbulb.integer(
        "max_reminders",
        "How many 24h lobby reminders before auto-kick (default: 3)",
        default=None,
        min_value=1,
        max_value=10,
    )
```

- [ ] **Step 2: Remove `max_reminders` from the `invoke` method** — delete these two blocks:

```python
        if self.max_reminders is not None:
            st.max_reminders = self.max_reminders
```

and

```python
        if self.max_reminders is not None:
            parts.append(f"max reminders: {self.max_reminders}")
```

- [ ] **Step 3: Remove `max_reminders` from the logger call** — change:

```python
        gc.logger.info(
            "Configured validation",
            lobby_channel=self.lobby_channel.name if self.lobby_channel else None,
            validate_category=self.validate_category.name
            if self.validate_category
            else None,
            member_role=self.member_role.name if self.member_role else None,
            staff_role=self.staff_role.name if self.staff_role else None,
            max_reminders=self.max_reminders,
        )
```

to:

```python
        gc.logger.info(
            "Configured validation",
            lobby_channel=self.lobby_channel.name if self.lobby_channel else None,
            validate_category=self.validate_category.name
            if self.validate_category
            else None,
            member_role=self.member_role.name if self.member_role else None,
            staff_role=self.staff_role.name if self.staff_role else None,
        )
```

- [ ] **Step 4: Update `ValidationStatus.invoke`** — replace:

```python
        lines.append(f"• Max reminders before kick: {st.max_reminders}")
```

with:

```python
        lines.append("• Validation timeout: 7 days from join, reminders every 18 hours")
```

- [ ] **Step 5: Run linting, type checking, and tests**

```bash
uv run ruff check dragonpaw_bot/plugins/validation/config.py && uv run ty check dragonpaw_bot/ && uv run pytest tests/test_validation.py -v
```

Expected: no errors, all tests PASS

- [ ] **Step 6: Commit**

```bash
git add dragonpaw_bot/plugins/validation/config.py
git commit -m "feat(validation): remove max_reminders config option"
```

---

### Task 6: Update CLAUDE.md

**Files:**
- Modify: `dragonpaw_bot/plugins/validation/CLAUDE.md`

- [ ] **Step 1: Update the Reminders / auto-kick section**

Replace:

```
5. **Reminders / auto-kick** — hourly cron checks members stuck at `AWAITING_RULES`. Every
   24 hours a lobby reminder is posted. After `max_reminders` reminders the member is kicked.
```

with:

```
5. **Reminders / timeout** — hourly cron checks members at `AWAITING_RULES` and `AWAITING_PHOTOS`.
   Every 18 hours a reminder is posted: lobby channel for `AWAITING_RULES`, validate channel for
   `AWAITING_PHOTOS`. After 7 days from `joined_at` the member is kicked and their validate channel
   (if any) is closed with a timeout notice. `AWAITING_STAFF` members are excluded — staff handles
   those manually. Constants: `REMINDER_INTERVAL_HOURS = 18`, `MAX_VALIDATION_DAYS = 7` in `cron.py`.
```

- [ ] **Step 2: Update the Configuration section**

Replace:

```
- **`setup [lobby_channel] [validate_category] [member_role] [staff_role] [max_reminders]`**
  — Set any combination. Omitted params keep current values. The welcome announcement channel is configured globally via `/config channels general`.
```

with:

```
- **`setup [lobby_channel] [validate_category] [member_role] [staff_role]`**
  — Set any combination. Omitted params keep current values. The welcome announcement channel is configured globally via `/config channels general`. Timeout (7 days) and reminder interval (18h) are hardcoded constants in `cron.py`.
```

- [ ] **Step 3: Update the State section**

Replace:

```
`ValidationGuildState` holds both config fields and the runtime `members` list.
Each `ValidationMember` tracks: `user_id`, `joined_at`, `reminder_count`,
`stage` (`ValidationStage` enum), `channel_id`, `photo_count`.
```

with:

```
`ValidationGuildState` holds both config fields and the runtime `members` list.
Each `ValidationMember` tracks: `user_id`, `joined_at`, `reminder_count` (18h pings sent so far),
`stage` (`ValidationStage` enum), `channel_id`, `photo_count`.
```

- [ ] **Step 4: Run tests one final time**

```bash
uv run pytest -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add dragonpaw_bot/plugins/validation/CLAUDE.md
git commit -m "docs(validation): update CLAUDE.md for 7-day timeout and 18h reminders"
```
