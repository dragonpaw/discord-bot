## Journal Plugin

A per-member record staff can read in one place: notes and warnings they file
by hand, alongside events other plugins already emit. The bot records and
renders — it never judges. There are no thresholds, no escalation, and nothing
is ever acted on automatically.

### Where the code lives

The **store lives in core**, at `dragonpaw_bot/journal.py`, not in this
package. That is because `gc.log()` writes to it, and `context.py` cannot
import a plugin without inverting the layering. This package owns only the
command surface, the config commands, and the name-change listener.

### Entry kinds

Four are **staff-authored** — filed by a human, and the only kinds that carry a
`WarningDetail` (reason, issuer, evidence, follow-ups):

| Kind | Emoji | Meaning |
|---|---|---|
| `note` | 📝 | A private staff observation. The member was not told. |
| `warning` | ⚠️ | The member was spoken to. |
| `ineligible` | 🚫 | Un-invited from graduation parties. |
| `eligible` | ✅ | Re-invited. |

Four are **observed** — emitted by other plugins, one-line summary only:

| Kind | Emoji | Source |
|---|---|---|
| `ticket_opened` | 🎫 | `plugins/tickets` |
| `birthday_set` | 🎂 | `plugins/birthdays` |
| `birthday_removed` | 🎂 | `plugins/birthdays` |
| `name_change` | 🏷️ | `listeners.py` in this package |

Kinds are distinguished by **emoji, not colour**: an embed has a single colour,
so a mixed-kind timeline cannot colour rows individually, and one embed per
entry would hit Discord's 10-embed message cap immediately.

### Append-only

Nothing is ever deleted or edited. There is no remove command and no pruning.
Corrections are **follow-ups** appended to the entry they correct, rendered
nested beneath it so a retraction cannot be missed while scrolling.

Accepted consequence: an entry filed in error stays forever, alongside any
verbatim message snapshot. There is no mechanism to honour a deletion request.

### Eligibility is derived, not stored

`journal.is_ineligible(guild_id, user_id)` reads the member's most recent
`ineligible`/`eligible` entry; no such entry means eligible. The fact lives in
exactly one place.

Eligibility is **purely informational**. Graduation parties are off-server IRL
events. No plugin reads this flag and nothing is blocked programmatically.

### Log-channel gating

| Situation | Observed events (via `gc.log`) | Staff-authored entries |
|---|---|---|
| No log channel configured | Not recorded | **Recorded** |
| Log channel set, Discord errors | Recorded | Recorded |
| Log channel set, normal | Recorded | Recorded |

An unconfigured log channel is the guild's opt-out signal, so `gc.log()` writes
nothing. A transient `HTTPError` is *not* that signal, so the entry is written
before the send is attempted. Staff-authored entries bypass `gc.log()` entirely
and are never gated — a warning a human sat down and typed is not noise.

### Commands

All staff-gated on `staff_role_id`, all responses ephemeral. Without a staff
role configured the plugin refuses and points at `/config journal set` rather
than falling open.

- **`/journal view <user>`** — Full timeline, newest first, follow-ups nested.
  Adds a field when the member is currently ineligible. Truncates with an
  explicit notice rather than exceeding Discord's 4096-character description.
- **`/journal add <user> <kind>`** — Modal for the reason. Kind is one of the
  four authored kinds; defaults to `warning`.
- **`/journal followup <id>`** — Modal; appends a follow-up. Refuses on
  observed entries, which carry no `detail` to attach to.
- **`/journal ineligible-list`** — Everyone currently ineligible, with reason
  and date.

**Right-click a message → Apps → "Log warning"** — the repo's first
`lightbulb.MessageCommand`. Files a `warning` against the message author with
the jump link, and pre-fills the message text into the modal. The text is
pre-filled rather than re-fetched on submit because capturing it before it can
be deleted is the entire point of the context menu.

`journal add`, `journal followup`, and `Log warning` are in
`_AUTO_DEFER_EXCLUSIONS` in `bot.py` — a modal cannot follow a deferred
response. `tests/test_journal.py` asserts the exclusion list matches the real
`qualified_name`s, which is the check that catches a rename.

### Configuration

`/config journal` (guild owner only):

- **`set staff_role:<role>`** — Role allowed to read and write the journal.
- **`status`** — Staff role, entry count, current ineligible count.
- **`clear`** — Clears the staff role. **Entries are never deleted.**

State is persisted to `state/journal_{guild_id}.yaml`.

### Writing from another plugin

Observed events ride `gc.log()`:

```python
await gc.log(
    f"🎫 I just opened a ticket for **{member.display_name}**! 🐾",
    journal_kind="ticket_opened",
    journal_user=member,
    journal_summary=f'Opened a ticket: "{topic}"',
)
```

`journal_kind` and `journal_user` must be passed **together** — supplying one
without the other raises `ValueError`, since a silently dropped entry is worse
than a crash. `journal_summary` defaults to the log message; pass it when the
staff-channel wording is too chatty for a timeline.

`journal_user` must be a `hikari.Member` (for `display_name`). When the subject
may have left the guild, resolve from cache and fall back to a plain `gc.log()`
— see `_log_birthday_event` in `plugins/birthdays/commands.py`.

### Name changes

`listeners.py` watches `hikari.MemberUpdateEvent`, which also fires on avatar
and role changes — `display_name_change()` is the delta filter that keeps every
role assignment out of the journal. These entries are written straight to the
store and **never posted to the log channel**; a few hundred renames a year
would drown it.

### No button channel card

Staff-driven, so no `BUTTON_ENTRY` — matching every other staff-only plugin.

### File Structure

- **`__init__.py`** — `MODAL_HANDLERS` exports only
- **`commands.py`** — `lightbulb.Loader()`, the `/journal` group, the message
  context menu, rendering helpers, and all modal handlers
- **`config.py`** — `/config journal` subcommands
- **`listeners.py`** — `MemberUpdateEvent` name-change listener
- **`../../journal.py`** — models, store, and write path (core, not this package)

### Scale

Roughly 100 members with a handful of records. That rules out pagination,
indexing, retention, and cleanup crons — all machinery for a problem this guild
does not have.
