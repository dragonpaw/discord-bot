# General Chat Channel + Intros Naughty List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bot-wide `general_channel_id` to `GuildState`, expose it via `/config channels`, migrate validation's per-plugin announce channel to use it, add general-chat fallback to media channel redirect notices, and add a weekly "naughty list" cron to the intros plugin.

**Architecture:** `GuildState` in `structs.py` gains `general_channel_id`. A new `/config channels` subgroup (replacing `/config bot`) provides `log` and `general` subcommands. Plugins that post to "general chat" read `general_channel_id` from `bot.state(guild_id)` — no per-plugin config field needed.

**Tech Stack:** hikari, hikari-lightbulb v3, Pydantic v2, structlog, pytest

---

## File Map

| File | Change |
|------|--------|
| `dragonpaw_bot/structs.py` | Add `general_channel_id` field |
| `dragonpaw_bot/bot.py` | Update `_yaml_dict_to_state`; rename `/config bot` → `/config channels`; rename `BotLogging` → `SetLogChannel`; add `SetGeneralChannel` |
| `dragonpaw_bot/plugins/validation/models.py` | Remove `general_channel_id` |
| `dragonpaw_bot/plugins/validation/commands.py` | Read `general_channel_id` from `bot.state()` |
| `dragonpaw_bot/plugins/validation/config.py` | Remove `announce_channel` param; update status display |
| `dragonpaw_bot/plugins/media_channels/__init__.py` | Fallback redirect to `general_channel_id` |
| `dragonpaw_bot/plugins/intros/cron.py` | Add `intros_weekly_naughty_list` task |
| `dragonpaw_bot/plugins/intros/CLAUDE.md` | Document naughty list cron |
| `dragonpaw_bot/plugins/validation/CLAUDE.md` | Update to reflect `general_channel_id` removal |
| `dragonpaw_bot/plugins/media_channels/CLAUDE.md` | Document general chat fallback |
| `tests/test_structs.py` | Test `general_channel_id` field |
| `tests/test_bot.py` | Test `_yaml_dict_to_state` with `general_channel_id` |

---

## Task 1: Add `general_channel_id` to `GuildState`

**Files:**
- Modify: `dragonpaw_bot/structs.py`
- Modify: `dragonpaw_bot/bot.py` (`_yaml_dict_to_state`, lines ~199–223)
- Test: `tests/test_structs.py`, `tests/test_bot.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_structs.py`, add after the existing tests:

```python
def test_guild_state_general_channel_id_defaults_to_none():
    state = GuildState(
        id=hikari.Snowflake(123456789),
        name="Test Guild",
        config_url="https://example.com/config.toml",
        config_last=datetime.datetime(2025, 1, 1, 12, 0, 0),
    )
    assert state.general_channel_id is None


def test_guild_state_general_channel_id_round_trip():
    state = GuildState(
        id=hikari.Snowflake(123456789),
        name="Test Guild",
        config_url="https://example.com/config.toml",
        config_last=datetime.datetime(2025, 1, 1, 12, 0, 0),
        general_channel_id=hikari.Snowflake(888),
    )
    dumped = state.model_dump()
    restored = GuildState.model_validate(dumped)
    assert restored.general_channel_id == hikari.Snowflake(888)
```

In `tests/test_bot.py`, add after `test_yaml_load_strips_legacy_role_fields`:

```python
def test_yaml_dict_round_trip_general_channel_id(state_dir):
    import yaml
    raw = {
        "id": 99,
        "name": "Test Guild",
        "config_url": "https://example.com/config.toml",
        "config_last": "2025-06-01T00:00:00",
        "log_channel_id": None,
        "general_channel_id": 555,
    }
    yaml_file = state_dir / "99.yaml"
    with open(yaml_file, "w") as f:
        yaml.dump(raw, f)

    loaded = state_load_yaml(hikari.Snowflake(99))
    assert loaded is not None
    assert loaded.general_channel_id == hikari.Snowflake(555)
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_structs.py::test_guild_state_general_channel_id_defaults_to_none tests/test_structs.py::test_guild_state_general_channel_id_round_trip tests/test_bot.py::test_yaml_dict_round_trip_general_channel_id -v
```

Expected: FAIL — `GuildState` has no `general_channel_id` field.

- [ ] **Step 3: Add `general_channel_id` to `GuildState`**

In `dragonpaw_bot/structs.py`, add after `log_channel_id`:

```python
    log_channel_id: hikari.Snowflake | None = None
    general_channel_id: hikari.Snowflake | None = None
```

- [ ] **Step 4: Update `_yaml_dict_to_state` in `bot.py`**

After the `log_channel_id` cast block (around line 208), add:

```python
    if data.get("general_channel_id") is not None:
        data["general_channel_id"] = hikari.Snowflake(data["general_channel_id"])
```

- [ ] **Step 5: Run tests to verify they pass**

```
uv run pytest tests/test_structs.py::test_guild_state_general_channel_id_defaults_to_none tests/test_structs.py::test_guild_state_general_channel_id_round_trip tests/test_bot.py::test_yaml_dict_round_trip_general_channel_id -v
```

Expected: PASS

- [ ] **Step 6: Run full test suite to check for regressions**

```
uv run pytest
```

Expected: all passing

- [ ] **Step 7: Commit**

```bash
git add dragonpaw_bot/structs.py dragonpaw_bot/bot.py tests/test_structs.py tests/test_bot.py
git commit -m "feat: add general_channel_id to GuildState"
```

---

## Task 2: `/config channels` command group

**Files:**
- Modify: `dragonpaw_bot/bot.py` (lines ~354–421)

- [ ] **Step 1: Rename `_bot_sub` to `_channels_sub` and update the subgroup**

In `bot.py`, replace:

```python
_bot_sub = _config_group.subgroup("bot", "Bot-wide settings")
```

with:

```python
_channels_sub = _config_group.subgroup("channels", "Channel settings")
```

- [ ] **Step 2: Rename `BotLogging` to `SetLogChannel` with subcommand name `log`**

Replace the class definition:

```python
class BotLogging(
    lightbulb.SlashCommand,
    name="logging",
    description="Set or clear the bot's log channel for this server.",
    hooks=[guild_owner_only],
):
```

with:

```python
class SetLogChannel(
    lightbulb.SlashCommand,
    name="log",
    description="Set or clear the bot's log channel for this server.",
    hooks=[guild_owner_only],
):
```

- [ ] **Step 3: Add `SetGeneralChannel` command**

After the `SetLogChannel` class (before `_bot_sub.register(BotLogging)`), add:

```python
class SetGeneralChannel(
    lightbulb.SlashCommand,
    name="general",
    description="Set or clear the general chat channel for this server.",
    hooks=[guild_owner_only],
):
    channel = lightbulb.channel(
        "channel", "General chat channel (omit to clear)", default=None
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        if not ctx.guild_id:
            logger.error("Interaction without a guild")
            return

        gc = GuildContext.from_ctx(ctx)
        guild = await gc.fetch_guild()
        state = gc.state()
        if not state:
            state = structs.GuildState(
                id=ctx.guild_id,
                name=guild.name,
                config_url="",
                config_last=datetime.datetime.now(tz=datetime.UTC),
            )

        if self.channel is not None:
            state.general_channel_id = self.channel.id
            bot.state_update(state)
            gc.logger.info("Set general chat channel", channel=self.channel.name)
            await ctx.respond(
                f"General chat channel set to <#{self.channel.id}>.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
        else:
            state.general_channel_id = None
            bot.state_update(state)
            gc.logger.info("Cleared general chat channel")
            await ctx.respond(
                "General chat channel cleared.", flags=hikari.MessageFlag.EPHEMERAL
            )
```

- [ ] **Step 4: Update the registration block**

Replace:

```python
_bot_sub.register(BotLogging)
```

with:

```python
_channels_sub.register(SetLogChannel)
_channels_sub.register(SetGeneralChannel)
```

- [ ] **Step 5: Run the test suite**

```
uv run pytest
```

Expected: all passing

- [ ] **Step 6: Type-check**

```
uv run ty check dragonpaw_bot/
```

Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add dragonpaw_bot/bot.py
git commit -m "feat: rename /config bot to /config channels, add general chat channel command"
```

---

## Task 3: Validation — remove `general_channel_id`, read from `GuildState`

**Files:**
- Modify: `dragonpaw_bot/plugins/validation/models.py`
- Modify: `dragonpaw_bot/plugins/validation/commands.py`
- Modify: `dragonpaw_bot/plugins/validation/config.py`

- [ ] **Step 1: Remove `general_channel_id` from `ValidationGuildState`**

In `dragonpaw_bot/plugins/validation/models.py`, remove this line:

```python
    general_channel_id: int | None = None
```

- [ ] **Step 2: Update `handle_approve_modal` to read `general_channel_id` from `GuildState`**

In `dragonpaw_bot/plugins/validation/commands.py`, inside `handle_approve_modal`, replace:

```python
    if st.general_channel_id:
        try:
            await bot.rest.create_message(
                channel=st.general_channel_id,
                content=(
                    ...
                ),
            )
        except hikari.HTTPError:
            gc.logger.warning(
                "Failed to post welcome announcement", channel_id=st.general_channel_id
            )
            await gc.log(
                f"⚠️ Couldn't post the welcome announcement for **{name}** in <#{st.general_channel_id}>! 🐉"
            )
```

with:

```python
    bot_st = bot.state(interaction.guild_id)
    general_channel_id = bot_st.general_channel_id if bot_st else None
    if general_channel_id:
        try:
            await bot.rest.create_message(
                channel=general_channel_id,
                content=(
                    f"🎉 *does a happy little dragon wiggle* Everyone say hello to **{name}**! "
                    f"They're officially part of the hoard now~ 🐉\n\n"
                    f"**{name}**, welcome welcome welcome!! A few things to get you settled in:\n"
                    f"• Peek at {f'<#{st.about_channel_id}>' if st.about_channel_id else '#about'} to learn more about us 📖\n"
                    f"• I'll see you over in {f'<#{st.roles_channel_id}>' if st.roles_channel_id else '#roles'} to help pick out your roles — grab some shiny ones! ✨\n"
                    f"• Tell us a little about yourself in {f'<#{st.intros_channel_id}>' if st.intros_channel_id else '#introductions'} 🐾\n"
                    f"• We host classes and have a SubDay Journal program — check out {f'<#{st.events_channel_id}>' if st.events_channel_id else '#classes-and-events'}! 📚\n"
                    f"• One tiny thing! I have a *very* hungry tummy for text in the media channels 🍽️ "
                    f"*nom nom* Images and links are yummy, but please pop your comments over in {f'<#{st.chat_channel_id}>' if st.chat_channel_id else '#general-often-lewd'}~ 💜"
                ),
            )
        except hikari.HTTPError:
            gc.logger.warning(
                "Failed to post welcome announcement", channel_id=int(general_channel_id)
            )
            await gc.log(
                f"⚠️ Couldn't post the welcome announcement for **{name}** in <#{general_channel_id}>! 🐉"
            )
```

- [ ] **Step 3: Remove `announce_channel` from `ValidationSetup`**

In `dragonpaw_bot/plugins/validation/config.py`:

**Remove** the option attribute:
```python
    announce_channel = lightbulb.channel(
        "announce_channel",
        "Channel where approved members are announced",
        default=None,
        channel_types=[hikari.ChannelType.GUILD_TEXT],
    )
```

**Remove** the assignment block inside `invoke`:
```python
        if self.announce_channel is not None:
            st.general_channel_id = int(self.announce_channel.id)
```

**Remove** the logging kwarg:
```python
            announce_channel=self.announce_channel.name
            if self.announce_channel
            else None,
```

**Remove** from `parts`:
```python
        if self.announce_channel:
            parts.append(f"announce: <#{self.announce_channel.id}>")
```

- [ ] **Step 4: Remove announce channel line from `ValidationStatus`**

In `dragonpaw_bot/plugins/validation/config.py`, inside `ValidationStatus.invoke`, remove:

```python
        lines.append(
            f"• Announce channel: {f'<#{st.general_channel_id}>' if st.general_channel_id else 'not set'}"
        )
```

- [ ] **Step 5: Run the test suite**

```
uv run pytest
```

Expected: all passing

- [ ] **Step 6: Type-check**

```
uv run ty check dragonpaw_bot/
```

Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add dragonpaw_bot/plugins/validation/models.py dragonpaw_bot/plugins/validation/commands.py dragonpaw_bot/plugins/validation/config.py
git commit -m "refactor(validation): read general_channel_id from GuildState instead of per-plugin config"
```

---

## Task 4: Media channels — general chat fallback for redirect

**Files:**
- Modify: `dragonpaw_bot/plugins/media_channels/__init__.py`

- [ ] **Step 1: Update redirect resolution in `on_message`**

In `dragonpaw_bot/plugins/media_channels/__init__.py`, inside `on_message`, replace:

```python
    redirect_hint = (
        f" Why not share your thoughts in <#{entry.redirect_channel_id}>? 🐾"
        if entry.redirect_channel_id
        else ""
    )
```

with:

```python
    bot_st = bot.state(event.guild_id)
    redirect_id = entry.redirect_channel_id or (
        bot_st.general_channel_id if bot_st else None
    )
    redirect_hint = (
        f" Why not share your thoughts in <#{redirect_id}>? 🐾"
        if redirect_id
        else ""
    )
```

- [ ] **Step 2: Run the test suite**

```
uv run pytest
```

Expected: all passing

- [ ] **Step 3: Type-check**

```
uv run ty check dragonpaw_bot/
```

Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add dragonpaw_bot/plugins/media_channels/__init__.py
git commit -m "feat(media): fall back to general chat channel for redirect hint in nom notice"
```

---

## Task 5: Intros — weekly naughty list cron

**Files:**
- Modify: `dragonpaw_bot/plugins/intros/cron.py`

- [ ] **Step 1: Add the weekly naughty list task to `cron.py`**

In `dragonpaw_bot/plugins/intros/cron.py`, add after the existing `intros_daily_cleanup` task and `_cleanup_guild` helper. The complete addition (append to end of file):

```python
@loader.task(lightbulb.crontrigger("0 20 * * 6"))
async def intros_weekly_naughty_list(bot: hikari.GatewayBot) -> None:
    """Weekly task: post naughty list of members who haven't introduced themselves."""
    bot = cast("DragonpawBot", bot)
    guilds = list(bot.cache.get_guilds_view().values())
    logger.debug("Intros weekly naughty list run", guild_count=len(guilds))

    for guild in guilds:
        try:
            await _naughty_list_guild(bot, guild)
        except Exception:
            logger.exception("Error during intros naughty list", guild=guild.name)


async def _naughty_list_guild(bot: DragonpawBot, guild: hikari.Guild) -> None:
    gc = GuildContext.from_guild(bot, guild)
    st = intros_state.load(int(guild.id))

    if st.channel_id is None:
        return

    bot_st = bot.state(guild.id)
    if not bot_st or not bot_st.general_channel_id:
        return

    # Collect user IDs who have posted in the intros channel
    posted_ids: set[int] = set()
    async for message in bot.rest.fetch_messages(st.channel_id):
        if not message.author.is_bot:
            posted_ids.add(int(message.author.id))

    # Collect eligible members (non-bot, with required role if configured)
    missing_members: list[hikari.Member] = []
    async for member in bot.rest.fetch_members(guild.id):
        if member.is_bot:
            continue
        if st.required_role_id is not None and st.required_role_id not in [
            int(r) for r in member.role_ids
        ]:
            continue
        if int(member.id) not in posted_ids:
            missing_members.append(member)

    logger.info(
        "Intros naughty list",
        guild=guild.name,
        missing_count=len(missing_members),
    )

    if not missing_members:
        try:
            await bot.rest.create_message(
                channel=bot_st.general_channel_id,
                content=(
                    "*does a happy wiggle* 🐉 Everyone in the hoard has posted an introduction — "
                    "I'm so proud of you all! Such good mammals! 🐾"
                ),
            )
        except hikari.HTTPError:
            logger.warning(
                "Failed to post naughty list all-clear",
                guild=guild.name,
                channel_id=int(bot_st.general_channel_id),
            )
        await gc.log(
            "📋 *happy tail wag* Weekly intros check — everyone's posted! 🐉"
        )
        return

    role_note = (
        f" with role **{st.required_role_name}**" if st.required_role_id else ""
    )
    mentions = " ".join(m.mention for m in missing_members)
    try:
        await bot.rest.create_message(
            channel=bot_st.general_channel_id,
            content=(
                f"*squints with clipboard* 🐉 Psst! These lovely folks{role_note} haven't introduced "
                f"themselves in <#{st.channel_id}> yet — give 'em a little nudge! 🐾\n{mentions}"
            ),
        )
    except hikari.HTTPError:
        logger.warning(
            "Failed to post naughty list",
            guild=guild.name,
            channel_id=int(bot_st.general_channel_id),
        )
        await gc.log(
            f"⚠️ Couldn't post the weekly intro naughty list in <#{bot_st.general_channel_id}>! 🐉"
        )
        return

    await gc.log(
        f"📋 Weekly intros check — **{len(missing_members)}** member(s){role_note} "
        f"still haven't posted in <#{st.channel_id}> 🐉"
    )
```

- [ ] **Step 2: Run the test suite**

```
uv run pytest
```

Expected: all passing

- [ ] **Step 3: Type-check**

```
uv run ty check dragonpaw_bot/
```

Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add dragonpaw_bot/plugins/intros/cron.py
git commit -m "feat(intros): add weekly naughty list cron posting to general chat"
```

---

## Task 6: Update CLAUDE.md files

**Files:**
- Modify: `dragonpaw_bot/plugins/intros/CLAUDE.md`
- Modify: `dragonpaw_bot/plugins/validation/CLAUDE.md`
- Modify: `dragonpaw_bot/plugins/media_channels/CLAUDE.md`

- [ ] **Step 1: Update `intros/CLAUDE.md`**

In the **Daily Cleanup Cron** section header, change to **Cron Tasks**. Add a new subsection after the daily cleanup description:

```markdown
### Weekly Naughty List Cron

Runs at 8pm UTC Saturday (`0 20 * * 6` = noon PST / 1pm PDT). For each configured guild:

1. Checks that `channel_id` is configured — skips if not.
2. Reads `GuildState.general_channel_id` via `bot.state(guild_id)` — skips if not set.
3. Same filter logic as `/intros missing`: fetches all messages, collects poster IDs, finds members (with required role if set) who haven't posted.
4. If nobody missing: posts an all-clear celebration message to the general channel.
5. If members missing: posts @mentions with a "naughty list" message to the general channel.
6. Logs summary to `gc.log()`.
```

Also add to the **Logging** section:
```
- **Info:** Weekly naughty list results
```

Update the **File Structure** entry for `cron.py`:
```
- **`cron.py`** — Daily cleanup cron task; weekly naughty list cron task
```

- [ ] **Step 2: Update `validation/CLAUDE.md`**

In the **Configuration** section, remove the `announce_channel` mention from the `setup` command description. It currently reads:

```
- **`setup [lobby_channel] [validate_category] [announce_channel] [member_role] [staff_role] [max_reminders]`**
```

Change to:

```
- **`setup [lobby_channel] [validate_category] [member_role] [staff_role] [max_reminders]`**
```

Add a note: The welcome announcement channel is now configured globally via `/config channels general`.

In the **State** section, remove `general_channel_id` from the fields list if mentioned.

- [ ] **Step 3: Update `media_channels/CLAUDE.md`**

In the **Enforcement Flow** section, update step 4:

```markdown
4. Post a playful dragon notice mentioning the user (with redirect hint if configured — per-channel redirect takes priority, falls back to the bot-wide general chat channel set via `/config channels general`).
```

Update the **Notice Copy** section to note the fallback:

```markdown
The redirect hint uses the per-channel redirect if configured, otherwise falls back to the bot-wide `general_channel_id` from `GuildState`. Omitted if neither is set.
```

- [ ] **Step 4: Commit**

```bash
git add dragonpaw_bot/plugins/intros/CLAUDE.md dragonpaw_bot/plugins/validation/CLAUDE.md dragonpaw_bot/plugins/media_channels/CLAUDE.md
git commit -m "docs: update plugin CLAUDE.md files for general chat channel changes"
```
