# Add a "Log note" message context menu

The journal ships with one message context-menu entry, **Log warning**
(`plugins/journal/commands.py`, `LogWarningMessageCommand`). It files
`kind="warning"` against the message author, capturing the message text
and jump link.

There is no equivalent for `kind="note"` — the quiet "we noticed this,
nobody was spoken to" record. Filing a note about a specific message
today means running `/journal add user:… kind:note` and pasting a link
by hand, which is the slow path the context menu exists to avoid.

## Why it was not built up front

Speculation. Nothing had demonstrated that staff wanted to note a
specific message rather than warn about one, and `code-simplicity.md`
says do not write code the current task does not require. Shipping one
entry and waiting to see whether the second is missed was the cheaper
bet.

## What it would take

Small. `LogWarningMessageCommand` already does everything except the
kind:

- A second `lightbulb.MessageCommand` named "Log note", identical
  except for `kind="note"` in `journal.record()`.
- A distinct modal prefix (e.g. `journal_note_msg_modal:`) registered
  in `MODAL_HANDLERS`, or reuse the warning handler with the kind
  encoded in the `custom_id` alongside the three snowflakes — there is
  room under Discord's 100-char cap.
- The new command name added to `_AUTO_DEFER_EXCLUSIONS` in `bot.py`;
  `tests/test_journal.py` already asserts that list matches real
  `qualified_name`s, so a miss fails the suite.

Discord allows up to five message commands per app, so two is fine.

## Trigger

Build it when someone actually reaches for it. If a few months pass
and nobody has wanted to note a message, that is the answer and this
file should be deleted.
