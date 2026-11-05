# Automatic Lurker Purge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recurring, staff-gated lurker purges. The bot freezes a list of lurkers,
staff spare people from a dropdown over a grace period, and a staff member
presses Boot to DM and kick whoever is left.

**Architecture:** Everything lives in the activity plugin.
- **Settings and live purge state:** new fields on `ActivityGuildConfig` and
  `ActivityGuildMeta` (persisted in `state/activity_config_{guild_id}.yaml`).
- **`plugins/activity/purge.py` (new):** owns the purge. Pure decision and
  rendering functions, a daily step called from the existing 04:15 UTC cron,
  and the two component handlers.
- **`/config lurker`:** a direct subcommand of `/config` that replaces
  `/config activity lurker`.

**Tech Stack:** Python 3.13, hikari, hikari-lightbulb v3, pydantic v2, structlog, pytest (asyncio auto mode).

**Spec:** `docs/superpowers/specs/2026-11-04-lurker-purge-design.md`

## Global Constraints

- Run `uv run ruff check dragonpaw_bot/ tests/`, `uv run ty check dragonpaw_bot/` and `uv run pytest` before every commit; all must pass.
- `code-simplicity.md` rules are mandatory: no speculative options, no unrequested error handling, no what-comments.
- **Defaults:** frequency is 4 months and grace is 5 days. Boot expires after 14 days.
- **Copy:** all user- and staff-facing copy uses the dragon persona: first-person, warm, never scolding. Each staff-channel message has a unique leading emoji. Literal emoji in source, never escapes.
- Component handlers must respond (or defer) before any slow work (DMs, kicks, log posts). The 3-second rule applies.
- structlog with keyword args. Log members by `display_name`.
- `/config lurker` options are all optional; omission means unchanged.
- Spares are one-way: there is no un-spare and no reason prompt.
- A purge's list is frozen at start: people who become lurkers later are never added, and activity never removes anyone.
- Only the guild owner or the purge staff role may spare or Boot.
- Commit straight to `main` (repo rule); don't push. Pushing happens at ship time.

## Review Focus

- **Two staff press Boot near-simultaneously:** exactly one Boot run happens. The second presser gets a kind "already done" reply. Pinned in Task 5.
- **A spare arrives after Boot ran, or after the purge expired or was cancelled** (stale dropdown on an old post): a kind ephemeral rejection, and nothing changes. Pinned in Task 4.
- **A candidate left the server before Boot:** they get no DM and no kick attempt, and the summary lists them as left. Pinned in Task 5.
- **More than 25 at-risk candidates:** the dropdown offers the first 25 unspared names, and later names become pickable as people are spared. Pinned in Task 3.
- **A kick is refused** (role hierarchy, or missing Kick Members): the other kicks still happen, and the summary names the failures with fix instructions. Pinned in Task 5.

---

## File Structure

| File | Responsibility |
|---|---|
| `dragonpaw_bot/plugins/activity/models.py` | New config fields, `PurgeCandidate`, `LurkerPurge`, `add_months()`, `PURGE_BOOT_EXPIRY_DAYS` |
| `dragonpaw_bot/plugins/activity/cron.py` | Newcomer rule in the lurker sync; call the purge daily step |
| `dragonpaw_bot/plugins/activity/purge.py` (new) | Candidate selection, schedule decision, rendering, daily step, start/cancel, spare and Boot handlers |
| `dragonpaw_bot/plugins/activity/config.py` | Remove `ActivityLurker`; add the `LurkerConfig` command (`/config lurker`) |
| `dragonpaw_bot/plugins/activity/__init__.py` | Register the spare and Boot custom IDs |
| `dragonpaw_bot/journal.py` | `lurker_spared` and `lurker_kicked` entry kinds |
| `dragonpaw_bot/bot.py` | Register `LurkerConfig` on `_config_group` |
| `dragonpaw_bot/plugins/activity/CLAUDE.md`, `CLAUDE.md` | Docs |
| `tests/test_activity.py`, `tests/test_activity_purge.py` (new) | Tests |

---

### Task 1: Settings fields and the newcomer rule in the lurker sync

**Files:**
- Modify: `dragonpaw_bot/plugins/activity/models.py` (`ActivityGuildConfig`, plus a new helper)
- Modify: `dragonpaw_bot/plugins/activity/cron.py` (`_evaluate_lurker` and its caller in `_sync_lurker_role`)
- Test: `tests/test_activity.py`

**Interfaces:**
- Produces (models.py):
  - `ActivityGuildConfig` gains:
    - `purge_enabled: bool = False`
    - `purge_interval_months: int = 4`
    - `purge_grace_days: int = 5`
    - `purge_staff_role_id: int | None = None`
  - `def add_months(dt: datetime.datetime, months: int) -> datetime.datetime`.
    Calendar months; negative values go backwards. The day clamps to the
    target month's last day (Jan 31 + 1 → Feb 28/29). Keeps tzinfo.
- Produces (cron.py): `_evaluate_lurker(role_ids: list[int], score: float, meta: ActivityGuildMeta, joined_at: datetime.datetime, now: datetime.datetime) -> tuple[bool, str]`.
  The order is immune → newcomer → score. A newcomer is someone with
  `joined_at > add_months(now, -meta.config.purge_interval_months)`; they return
  `(False, "still new")`. The rule applies whether or not purges are enabled.

- [ ] **Step 1: Write failing tests** in `tests/test_activity.py`:
  - `test_add_months_clamps_day`: `add_months(datetime(2026,1,31,tzinfo=UTC), 1) == datetime(2026,2,28,tzinfo=UTC)`.
  - `test_add_months_negative_crosses_year`: `add_months(datetime(2026,2,15,tzinfo=UTC), -4) == datetime(2025,10,15,tzinfo=UTC)`.
  - `test_evaluate_lurker_newcomer_never_lurker`: score 0, joined 30 days before `now`, default config → `(False, "still new")`.
  - `test_evaluate_lurker_old_member_low_score_is_lurker`: score 0, joined 5 months before `now` → `(True, "no longer active")`.
  - `test_evaluate_lurker_immunity_beats_newcomer`: an ignored role and joined yesterday → `(False, "gained immunity")`.
  - `test_purge_config_defaults`: `ActivityGuildConfig()` has `purge_enabled is False`, `purge_interval_months == 4`, `purge_grace_days == 5`, `purge_staff_role_id is None`.
- [ ] **Step 2: Run** `uv run pytest tests/test_activity.py -k "add_months or evaluate_lurker or purge_config" -v`. Expected: FAIL (ImportError or AttributeError).
- [ ] **Step 3: Implement** the fields, `add_months`, and the new `_evaluate_lurker` signature.
  - In `_sync_lurker_role`, pass `member.joined_at` and `now_dt = datetime.datetime.fromtimestamp(now, tz=datetime.UTC)`.
  - Update the docstring's transition labels to include `still new`.
- [ ] **Step 4: Run** the tests from Step 2, then the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(activity): never tag members newer than the purge interval as lurkers`.

---

### Task 2: Purge state, journal kinds, candidate selection, schedule decision

**Files:**
- Modify: `dragonpaw_bot/plugins/activity/models.py`
- Modify: `dragonpaw_bot/journal.py` (`EntryKind`, `KIND_EMOJI`)
- Create: `dragonpaw_bot/plugins/activity/purge.py`
- Test: `tests/test_activity_purge.py` (new)

**Interfaces:**
- Consumes: `add_months` and the config fields from Task 1.
- Produces (models.py):
  ```python
  PURGE_BOOT_EXPIRY_DAYS = 14

  class PurgeCandidate(pydantic.BaseModel):
      user_id: int
      display_name: str
      spared_by: str | None = None      # staff display name; set once, never cleared

  class LurkerPurge(pydantic.BaseModel):
      started_at: datetime.datetime
      day: int = 1                      # grace day; grace_days + 1 means the Boot post is up
      boot_posted_at: datetime.datetime | None = None
      message_id: int | None = None     # latest post carrying components (to strip later)
      candidates: list[PurgeCandidate]
  ```
  `ActivityGuildMeta` gains:
  - `purge: LurkerPurge | None = None` (the purge in progress)
  - `last_purge_started: datetime.datetime | None = None` (the schedule anchor)
- Produces (journal.py): `EntryKind` gains `"lurker_spared"` and `"lurker_kicked"`. `KIND_EMOJI` maps them to `"🛡️"` and `"👢"`.
- Produces (purge.py):
  - `PurgeAction = Literal["start", "remind", "final", "expire"]`
  - `def select_candidates(members: Iterable[hikari.Member], lurker_role_id: int) -> list[PurgeCandidate]`
    returns non-bot holders of the role, sorted by `display_name.casefold()`.
  - `def next_action(meta: ActivityGuildMeta, now: datetime.datetime) -> PurgeAction | None`. The decision table:

    | State | Returns |
    |---|---|
    | disabled | `None` |
    | no purge, never started | `"start"` |
    | no purge, and `now >= add_months(last_purge_started, interval)` | `"start"` |
    | no purge otherwise | `None` |
    | purge in grace, and `day + 1 <= grace_days` | `"remind"` |
    | purge in grace, and `day + 1 == grace_days + 1` | `"final"` |
    | Boot posted, and `now - boot_posted_at >= 14 days` | `"expire"` |
    | Boot posted otherwise | `None` |

    `next_action` only decides. The caller applies `day += 1` for `"remind"`
    and `"final"`.

- [ ] **Step 1: Write failing tests** in `tests/test_activity_purge.py`:
  - `test_select_candidates_only_role_holders_sorted`: members `bob` (role), `Alice` (role), `carol` (no role), and a bot with the role → names `["Alice", "bob"]`.
  - `test_next_action_disabled_is_none`.
  - `test_next_action_first_start_when_never_run`.
  - `test_next_action_waits_until_interval`: `last_purge_started` 3 months ago with interval 4 → `None`. At exactly 4 months → `"start"`.
  - `test_next_action_remind_then_final`: grace 5. Day 1 → `"remind"`; day 4 → `"remind"`; day 5 → `"final"`.
  - `test_next_action_expire_after_14_days`: `boot_posted_at` 13 days ago → `None`. 14 days ago → `"expire"`.
  - `test_meta_round_trips_with_purge`: `ActivityGuildMeta` with a `LurkerPurge` (one spared candidate) survives `model_dump(mode="json")` → `model_validate`.
- [ ] **Step 2: Run** `uv run pytest tests/test_activity_purge.py -v`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement** the models, the journal kinds, `select_candidates` and `next_action`.
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(activity): purge state model and schedule decision`.

---

### Task 3: Rendering and the daily step (start, remind, final, expire)

**Files:**
- Modify: `dragonpaw_bot/plugins/activity/purge.py`
- Modify: `dragonpaw_bot/plugins/activity/cron.py` (`_daily_guild`)
- Test: `tests/test_activity_purge.py`

**Interfaces:**
- Consumes: everything from Task 2.
- Produces (purge.py):
  - `SPARE_ID = "activity_purge_spare"`, `BOOT_ID = "activity_purge_boot"`
  - `MAX_MENU_OPTIONS = 25`
  - `def build_purge_post(bot: hikari.GatewayBot, purge: LurkerPurge, grace_days: int) -> tuple[hikari.Embed, list[hikari.api.ComponentBuilder]]`. Rules:
    - The embed lists every candidate.
    - Spared candidates render as `~~name~~ — spared by X 💜`.
    - In grace, the title shows days left (`grace_days - day + 1`).
    - From `boot_posted_at` on, the title says the final list is ready.
    - Components: one text menu (`SPARE_ID`) with up to 25 *unspared* candidates (value = user_id string), with `max_values = len(options)`. It is omitted when nobody is at risk.
    - When `boot_posted_at` is set, a DANGER button `BOOT_ID` labelled `Boot` is added.
  - `async def start_purge(gc: GuildContext, meta: ActivityGuildMeta, members: Iterable[hikari.Member], now: datetime.datetime) -> None`
    - Sets `meta.purge` (`day=1`) and `meta.last_purge_started = now`, then posts.
    - With zero candidates it posts a cheerful "nobody to purge" note instead, leaves `meta.purge` as `None`, and still sets `last_purge_started`.
  - `async def cancel_purge(gc: GuildContext, meta: ActivityGuildMeta) -> None`: strips components from the current post, posts a cancel note, and sets `meta.purge = None`.
  - `async def purge_daily_step(gc: GuildContext, meta: ActivityGuildMeta, members: Iterable[hikari.Member], now: datetime.datetime) -> None`: applies `next_action`, then saves via `activity_state.save_config`.
  - Private helper: `_post(gc, meta, purge)` strips components from `purge.message_id` (`edit_message(..., components=[])`; ignore `NotFoundError`), sends the new post to `gc.log_channel_id`, and stores the new `message_id`. With no log channel it logs a warning and posts nothing.
- `cron.py`: in `_daily_guild`, after the lurker sync block, call `await purge.purge_daily_step(gc, meta, members.values(), now_dt)` when `meta.config.lurker_role_id` is set. This runs even when the sync was skipped for short history.

- [ ] **Step 1: Write failing tests**:
  - `test_build_post_menu_lists_only_unspared`: 3 candidates, 1 spared → menu has 2 options, and the spared name shows in strikethrough in the embed.
  - `test_build_post_menu_caps_at_25`: 30 unspared → 25 options, the first 25 by list order. After sparing the first 5 → options start at candidate 6 and run through candidate 30.
  - `test_build_post_no_menu_when_all_spared`.
  - `test_build_post_boot_button_only_after_final`.
  - `test_daily_step_start_posts_and_freezes_list`: mock `gc.bot.rest.create_message` returning a message with `id`. Afterwards `meta.purge.candidates` match the role holders, and `last_purge_started == now`.
  - `test_daily_step_start_with_no_lurkers_sets_anchor_only`.
  - `test_daily_step_remind_strips_previous_components`: `edit_message` is awaited with the old `message_id` and `components=[]`, and `day` increments.
  - `test_daily_step_final_sets_boot_posted_at`.
  - `test_daily_step_expire_clears_purge_and_posts_note`.
  - `test_list_frozen_new_lurker_not_added`: start with A. A remind step with members now A and B (both with the role) → candidates are still only A.
  - `test_cancel_purge_clears_state`.
- [ ] **Step 2: Run** `uv run pytest tests/test_activity_purge.py -v`. Expected: the new tests fail.
- [ ] **Step 3: Implement.** Copy guide (wording is yours; persona and unique emojis are required):
  - start / remind: 🍽️
  - final: 🥢
  - expire: ⌛
  - cancel: 🕊️
  - no lurkers: 🌟
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(activity): post and advance lurker purges from the daily cron`.

---

### Task 4: Spare dropdown handler

**Files:**
- Modify: `dragonpaw_bot/plugins/activity/purge.py`
- Modify: `dragonpaw_bot/plugins/activity/__init__.py` (`INTERACTION_HANDLERS[SPARE_ID]`)
- Test: `tests/test_activity_purge.py`

**Interfaces:**
- Consumes: `build_purge_post`, `SPARE_ID`, and the models.
- Produces:
  - `def is_purge_staff(meta: ActivityGuildMeta, member: hikari.Member, owner_id: int) -> bool`: true if the member is the owner or holds `purge_staff_role_id`. Cache-free.
  - `async def handle_spare(interaction: hikari.ComponentInteraction) -> None`

Behaviour, in order:
1. **Not staff:** ephemeral denial, plus a `logger.warning` permission-denial log.
2. **Stale dropdown:** no live purge, `interaction.message.id != purge.message_id`, or Boot already posted and pressed (`meta.purge is None`) → ephemeral "this list is no longer live".
3. **Otherwise:**
   - Set `spared_by = member.display_name` on each selected candidate not already spared, then save.
   - Respond with `MESSAGE_UPDATE`, using the re-rendered post.
   - Then, for each newly spared person, call `journal.record(..., kind="lurker_spared", summary=f"🛡️ Spared from the lurker purge by {staff}")` directly, not through `gc.log`.

Sparing stays allowed while the Boot post is up.

- [ ] **Step 1: Write failing tests:**
  - `test_spare_denied_for_non_staff`
  - `test_spare_marks_and_updates_message`: the response type is `MESSAGE_UPDATE` and the menu no longer offers the spared id.
  - `test_spare_is_one_way`: re-sparing an already spared id doesn't change `spared_by`.
  - `test_spare_rejected_after_purge_cleared`
  - `test_spare_rejected_on_old_message`
  - `test_spare_writes_journal_entry`: monkeypatch `journal.record` and assert `kind == "lurker_spared"`.
  - `test_spare_allowed_on_boot_post`
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** and register the handler.
- [ ] **Step 4: Run** the full suite, including `tests/test_buttons.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(activity): staff can spare members from a lurker purge`.

---

### Task 5: Boot handler

**Files:**
- Modify: `dragonpaw_bot/plugins/activity/purge.py`
- Modify: `dragonpaw_bot/plugins/activity/__init__.py` (`INTERACTION_HANDLERS[BOOT_ID]`)
- Test: `tests/test_activity_purge.py`

**Interfaces:**
- Consumes: `is_purge_staff`, the models, `utils.guild_member`.
- Produces: `async def handle_boot(interaction: hikari.ComponentInteraction) -> None`.

Behaviour, in order:
1. **Not staff:** ephemeral denial plus a warning log.
2. **No live purge, or Boot not posted yet:** ephemeral "already done".
3. **Claim the purge with no `await` before saving:** take `purge = meta.purge`, set `meta.purge = None`, save. This is the double-press guard.
4. **Respond `MESSAGE_UPDATE`:** the same post with no components, plus "booting…".
5. **For each unspared candidate:**
   - Look them up with `guild_member(bot, guild_id, user_id)`.
   - Gone → record as `left`.
   - Otherwise:
     1. DM the goodbye text (warm, no invite link). DM errors are logged and ignored.
     2. `rest.kick_user(guild_id, user_id, reason=f"Lurker purge — booted by {staff}")`.
     3. On success, `journal.record(..., kind="lurker_kicked", summary=f"👢 Booted in the lurker purge by {staff}")`.
     4. A `ForbiddenError` or `HTTPError` → record as `failed` and continue.
6. **One `gc.log` summary (💨):** kicked names, left names, and failed names. If any failed, add the fix text ("check my **Kick Members** permission and that my role sits above theirs").

- [ ] **Step 1: Write failing tests:**
  - `test_boot_denied_for_non_staff`
  - `test_boot_before_final_rejected`
  - `test_boot_second_press_is_rejected`: call `handle_boot` twice → `kick_user` is awaited only for the first run, and the second reply is ephemeral.
  - `test_boot_dms_before_kick`: use a shared mock call log to assert the DM send precedes `kick_user` for the same user.
  - `test_boot_skips_spared`
  - `test_boot_departed_member_not_dmed_or_kicked`
  - `test_boot_kick_forbidden_continues_and_reports`: the first kick raises `ForbiddenError`, the second succeeds, and the summary text contains both names under the right headings.
  - `test_boot_journal_and_audit_reason`: the reason contains the staff name, and `journal.record` is called with `kind="lurker_kicked"` only for kicked members.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** and register the handler.
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(activity): staff Boot kicks the remaining purge list`.

---

### Task 6: `/config lurker`, plus docs

**Files:**
- Modify: `dragonpaw_bot/plugins/activity/config.py`
  - remove `ActivityLurker` and its `register` line
  - add `LurkerConfig`
  - `/config activity status` keeps the lurker-role line, plus a hint `(see /config lurker)`
- Modify: `dragonpaw_bot/bot.py` (`_config_group.register(activity_config.LurkerConfig)`)
- Modify: `dragonpaw_bot/plugins/activity/CLAUDE.md`, `CLAUDE.md`
- Test: `tests/test_activity_purge.py`

**Interfaces:**
- Consumes: `start_purge`, `cancel_purge`, `check_role_manageable`, `check_guild_perms`, `utils.guild_members`.
- Produces: `class LurkerConfig(lightbulb.SlashCommand, name="lurker", hooks=[guild_owner_only])` with options:

  | Option | Type | Notes |
  |---|---|---|
  | `role` | role | |
  | `enabled` | boolean | |
  | `frequency` | integer, 1–12 | months |
  | `grace` | integer, 1–14 | days |
  | `staff_role` | role | |

  Every option has `default=None`.

Behaviour:
1. **No options:** an ephemeral settings summary, including any purge in progress (day N of grace, or the Boot post is up).
2. **Apply the given options,** then validate:
   - `role` → `check_role_manageable` warning.
   - Enabling (`enabled=True` while previously off):
     - **Refuse,** keeping the old value, if there's no lurker role (after applying `role`) or no log channel (`gc.log_channel_id`). Use friendly text naming the missing piece.
     - **Otherwise warn** (and still save) if `check_guild_perms(... {KICK_MEMBERS: "Kick Members"})` reports it missing.
3. **Save,** respond ephemerally, then `gc.log` the change (⚙️).
4. **If it was just enabled:** fetch members and `await start_purge(...)`.
5. **If it was just disabled with a purge in progress:** `await cancel_purge(...)`.

Docs:
- **`plugins/activity/CLAUDE.md`:**
  - new "Lurker purges" section: flow, settings, staff role, expiry, journal kinds, and the custom IDs
  - newcomer rule in the lurker-sync list
  - `/config lurker` replacing `lurker @role`
  - `purge.py` in File Structure
  - new state fields
- **Root `CLAUDE.md`:** add `/config lurker (lurker role and purges)` to the subgroup list.
- **Delete the spec and plan files** (repo rule: finished specs are deleted, not archived). The commit message and CLAUDE.md are the record.

- [ ] **Step 1: Write failing tests:**
  - `test_lurker_config_refuses_enable_without_log_channel`
  - `test_lurker_config_refuses_enable_without_role`
  - `test_lurker_config_enable_starts_purge`: monkeypatch `purge.start_purge` and assert it was awaited.
  - `test_lurker_config_disable_cancels_running_purge`
  - `test_lurker_config_partial_update_keeps_other_fields`: setting only `grace=7` leaves frequency and staff role unchanged.

  Invoke `LurkerConfig.invoke` with a mocked `ctx`, following the existing config-command tests (e.g. `tests/test_subday_config.py`).
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** the command and the bot.py wiring, then update the docs.
- [ ] **Step 4: Run** ruff, ty and the full pytest suite. Expected: all pass.
- [ ] **Step 5: Commit** `feat(activity): /config lurker with automatic purges`.
