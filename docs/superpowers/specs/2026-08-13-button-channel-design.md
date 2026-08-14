# Button Channel — Design

**Date:** 2026-08-13
**Status:** Approved, ready for implementation planning

## Purpose

Members can't discover slash commands. The bot's most useful public actions —
opening a support ticket, joining the journal program, registering a birthday —
are reachable only by typing a command nobody remembers. This adds a single
channel that hosts one pretty embed per plugin, each with a button that runs
that plugin's main public action.

This is the same idea as the existing role channel, generalised across plugins.

## Scope

In scope: a core registry, a renderer, a `/config buttons` subgroup, and button
entries for four plugins (tickets, subday, birthdays, activity).

Out of scope: entries for `validation`, `role_menus`, `intros`,
`media_channels`, `channel_cleanup`. The first two already own dedicated
channels with their own flows; the last three are staff- or cron-driven and have
no public action worth a button.

## Architecture

### `ButtonEntry` — `dragonpaw_bot/structs.py`

Frozen dataclasses, not Pydantic models. These are code-defined constants that
are never parsed from user input or persisted, so Pydantic validation buys
nothing and `Callable` fields would force `arbitrary_types_allowed`.

```python
@dataclass(frozen=True)
class ButtonSpec:
    custom_id: str
    label: str
    emoji: str


@dataclass(frozen=True)
class ButtonEntry:
    key: str                                 # "tickets" — for logs and status output
    title: str                               # embed title
    description: str                         # embed description
    color: hikari.Color
    buttons: tuple[ButtonSpec, ...]          # 1 or 2
    is_available: Callable[[int], bool]      # guild_id -> should this entry show?
```

They live in `structs.py` so a plugin can declare its entry by importing
`structs` alone. If they lived in `buttons.py` — which imports every plugin —
declaring an entry would create an import cycle.

### `dragonpaw_bot/buttons.py` — new core module

Holds three things:

1. **The registry** — an ordered list built from each plugin's `BUTTON_ENTRY`,
   aggregated exactly the way `bot.py` aggregates `INTERACTION_HANDLERS`:

   ```python
   _ENTRIES: list[structs.ButtonEntry] = [
       tickets.BUTTON_ENTRY,
       subday.BUTTON_ENTRY,
       birthdays.BUTTON_ENTRY,
       activity.BUTTON_ENTRY,
   ]
   ```

   List order is display order. Tickets first because asking for help is the
   most time-sensitive action; activity last because it is a curiosity.

2. **`post_buttons(gc: GuildContext) -> list[str]`** — the renderer. Returns a
   list of warnings, matching `configure_role_menus()`'s contract.

3. **`register(subgroup)`** — wires `/config buttons` into the `/config` group,
   the same contract every plugin's `config.py` already implements.

This is a core module rather than a plugin because it imports from every plugin;
plugins do not currently import each other and this design does not change that.
It is a separate module rather than more code in `bot.py` because `bot.py` is
already 612 lines. `bot.py` gains three lines: the import, the subgroup, and the
`register()` call.

### Rendering

`post_buttons()` mirrors `configure_role_menus()` in
`plugins/role_menus/commands.py`:

1. Resolve the channel from `GuildState.button_channel_id`.
2. `ChannelContext.delete_my_messages()` to wipe the previous render.
3. For each entry where `is_available(guild_id)` is true, send one message
   carrying that entry's embed and a single action row of its buttons.

One message per entry, not one message with many embeds. Discord attaches an
action row to a *message*, not to an embed, so multiple embeds in one message
would leave every button in one undifferentiated row at the bottom. One message
per entry is what makes each card's buttons visibly belong to it.

State is a single new field, `GuildState.button_channel_id`. Message IDs are not
persisted: the render is wipe-and-repost, so there is nothing to track between
runs. This matches role menus.

### Interaction routing

The hub owns no behavior. Every button's `custom_id` is routed by the existing
prefix dispatcher in `bot.py` (`_INTERACTION_ROUTES`) to the plugin that
declared it. `buttons.py` never learns what a button does.

## The entries

| Plugin    | Colour               | Buttons                                              | Shown when                        |
| --------- | -------------------- | ---------------------------------------------------- | --------------------------------- |
| Tickets   | `SOLARIZED_ORANGE`   | 🆘 Ask an Adultier Adult                              | `staff_role_id` is set            |
| SubDay    | `SOLARIZED_VIOLET`   | 📖 Join Where I am Led · ❓ What is this?              | `subday_{guild_id}.yaml` exists   |
| Birthdays | `SOLARIZED_MAGENTA`  | 🎂 Add My Birthday                                    | `announcement_channel` is set     |
| Activity  | `SOLARIZED_CYAN`     | 📊 My Activity Score                                  | `activity_{guild_id}_config.yaml` exists |

Colours are fixed per plugin, taken from the existing `colors.py` constants,
rather than spread with `rainbow()`. A card keeps its colour when other entries
appear or disappear, so members learn to recognise it by sight.

Embed copy follows the project's dragon persona — warm, first-person, playful.

### Per-plugin handler work

Each plugin needs its command body extracted into a function that both the
slash command and a new button handler call. This is the right shape
independently of the hub; today the logic is trapped inside `invoke()`.

- **Tickets** — new `handle_ticket_open_button`, custom_id `ticket_open`,
  registered in `plugins/tickets/__init__.py`. Performs the required-role gate
  and duplicate-ticket check against the *cached* member payload, then responds
  with the topic modal. It must avoid REST round-trips for the same reason
  `AdultierAdultCommand` does: a modal cannot follow a deferred response, so the
  handler is bound by Discord's 3-second deadline. The gate logic is extracted
  from `AdultierAdultCommand.invoke` and shared.

- **SubDay join** — reuses the existing `SUBDAY_SIGNUP_ID` (`subday_signup`)
  custom_id verbatim. No new handler, no new route. Zero new code.

- **SubDay about** — extract the three-embed builder out of
  `SubDayAbout.invoke` into a function returning `list[hikari.Embed]`. New
  `handle_about_interaction`, custom_id `subday_about`, replies ephemerally with
  those embeds. The slash command calls the same builder.

- **Birthdays** — new `birthday:start` custom_id. `handle_tz_interaction`
  already branches on the suffix after `BIRTHDAY_PREFIX`, so this is one new
  branch that replies ephemerally with `_month_select_row()` — the same first
  step `BirthdaySet.invoke` posts. No new route registration needed; the
  `birthday:` prefix is already dispatched.

- **Activity** — extract the score lookup and embed construction from
  `ActivityScore.invoke` into `build_score_embed(bot, guild_id, member)`. New
  `handle_score_interaction`, custom_id `activity_score`, registered in
  `plugins/activity/__init__.py` (which currently exports no handlers, so
  `bot.py` gains an `activity_handlers` import). The button always shows the
  caller their own score, so it needs none of the viewer-role check that the
  slash command's `user` option requires.

### Supporting change: `ChannelContext.from_channel`

`post_buttons()` needs a `ChannelContext` for an arbitrary channel.
`ChannelContext.from_entry` takes a duck-typed object with `channel_id` and
`channel_name`, which forces callers who have neither a `CleanupChannelEntry`
nor a `MediaChannelEntry` to fake one. `role_menus/commands.py` does exactly
that today with an eight-line `type()` expression.

Add `ChannelContext.from_channel(gc, channel_id, channel_name)` and use it in
`buttons.py`, then replace the `role_menus` hack with a call to it. This is the
"redesign the piece so the feature drops in cleanly" step — without it there
would be two ways to build a `ChannelContext` for a plain channel, one of them
a metaprogramming trick.

## Config commands

Registered under `/config buttons`, owner-only, matching `/config channels log`:

- **`/config buttons channel [#channel]`** — sets `button_channel_id` and posts
  immediately. Omitting the channel clears the setting and wipes the bot's
  messages from the old channel.
- **`/config buttons refresh`** — re-runs `post_buttons()` against the
  configured channel.
- **`/config buttons status`** — reports the configured channel and lists every
  registered entry as showing or hidden.

`channel` validates with `check_channel_perms(..., CHANNEL_CLEANUP_PERMS)` —
the renderer both posts and deletes — and warns the admin while still saving,
per the project's stated permission-validation convention.

All three log to the guild log channel via `gc.log()` with a leading emoji and
first-person dragon voice.

## Availability

An entry is shown only when its plugin is configured for that guild. Tickets and
birthdays have a genuine config field to test. SubDay and activity have no
on/off flag, so their check falls back to "a state file exists for this guild",
which means an admin has run at least one `/config` command for that plugin.

This is indirect, and for SubDay the state file also appears the first time a
member signs up — so the entry can show in a guild whose admin never explicitly
enabled it. `/config buttons status` exists to make that diagnosable rather than
mysterious. Adding an explicit `enabled` flag to those two plugin configs was
considered and deliberately deferred: it is new persisted state serving a
problem that has not actually been observed.

## Error handling

- **Channel missing or unresolvable** — `post_buttons()` returns a warning; the
  config command surfaces it to the admin and `gc.log()`s it.
- **Missing permissions** — reported at config time as a warning that does not
  block saving. A runtime `ForbiddenError` during a repost is caught, logged,
  and posted to the guild log channel with actionable fix instructions, per the
  project convention for cron and background failures.
- **No available entries** — the channel is wiped and a single "nothing to show
  yet" note is posted, so an admin who points the bot at a channel and sees
  nothing understands why.
- **Click on a stale button** — the plugin handler already validates its own
  preconditions and responds ephemerally. Because the render is wipe-and-repost,
  stale buttons only survive if a repost failed partway; the handlers' existing
  guards cover it.

## Testing

New `tests/test_buttons.py`, following `tests/test_role_menus.py`:

- `is_available` returns the right answer for each of the four plugins across
  configured and unconfigured guild states.
- The registry's declared `custom_id`s all match a prefix in `bot.py`'s
  `_INTERACTION_ROUTES`. This is the test that matters most — it catches the
  failure mode where someone adds an entry and forgets to register its handler,
  which would otherwise only show up as an "Unhandled interaction" error in
  production.
- `post_buttons()` sends one message per available entry and skips unavailable
  ones.
- Entry keys are unique and every entry declares one or two buttons.

Extraction refactors are covered by the existing plugin tests, which must
continue to pass unchanged.

## Verification

- `uv run pytest`
- `uv run ty check dragonpaw_bot/`
- `uv run ruff check dragonpaw_bot/`
- `uv run ruff format dragonpaw_bot/`

## Documentation

- Root `CLAUDE.md`: a section describing the button channel and the
  `BUTTON_ENTRY` contract, plus entries in the architecture list and the
  `/config` subgroup list.
- Each touched plugin's `CLAUDE.md` gains its button entry and new handler, per
  the project rule that plugin docs are the source of truth for plugin
  behaviour.
