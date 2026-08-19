# Extract a shared modal_value() helper

Four plugins now hand-roll the same nested walk over
`interaction.components` to pull one text input's value out of a
submitted modal. The journal plugin (2026-08-18) was the fourth.

## The four copies

- `plugins/birthdays/commands.py` — day + wishlist modal
- `plugins/tickets/commands.py` — ticket topic modal
- `plugins/validation/commands.py` — validation modal
- `plugins/journal/commands.py` — `modal_value()`, the cleanest of
  the four and the only one that returns `""` rather than `None`
  when the field is missing

Each is the same shape: two nested `for` loops over
`interaction.components` and `row.components`, comparing
`component.custom_id` against a known field id.

## Why now and not before

`code-simplicity.md` says generalize on the second use case, not the
first — so the first three copies were correct as written. The fourth
crosses the line: the "how do I read a modal field" fact now lives in
four places, and a hikari change to the components structure would be
a four-file fix.

## Suggested shape

Move it to `utils.py` alongside the other Discord helpers:

```python
def modal_value(interaction: hikari.ModalInteraction, field: str) -> str:
    """The submitted value for a text input, or "" if absent."""
```

Then delete the four local versions. Note the callers disagree on the
missing-field case today: tickets treats a falsy topic as an error and
responds, birthdays and validation assume presence. Returning `""`
preserves the truthiness checks that already exist, so callers should
not need edits beyond the import — but check each one rather than
assuming.

## Why bother

It is a genuinely duplicated fact, not just similar-looking code, and
`tests/test_journal.py` already covers the journal copy's behaviour so
there is a reference for what the shared version must do.

## Cost

Touches three otherwise-stable plugin files. That is the reason it was
deferred rather than folded into the journal work — the journal diff
had no business editing validation. Worth doing as its own change,
with the full suite as the gate.
