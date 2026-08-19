# Small journal cleanups worth doing opportunistically

Three items surfaced by the simplifier and review passes on
2026-08-18 that were deliberately not applied. None is urgent; each is
individually too small to justify its own change, so fold them into
whatever next touches `plugins/journal/`.

## 1. The staff-gate preamble repeats five times

Every journal command opens with the same four lines:

```python
if not ctx.guild_id:
    return
st = journal.load(int(ctx.guild_id))
if refusal := staff_blocked(ctx, st.staff_role_id):
    logger.info("Journal … denied", actor=actor_name(ctx))
    await ctx.respond(refusal, flags=hikari.MessageFlag.EPHEMERAL)
    return
```

Folding it into `async def refuse_unless_staff(ctx) -> bool` would
delete roughly twenty lines.

**The argument against, which is why it was not done:** it changes
`staff_blocked` from a pure predicate into something that responds.
The current version is a pure function over `(ctx, staff_role_id)` and
has four focused tests (`tests/test_journal.py`). A merged version
would need an interaction mock to test the same ground. Twenty lines is
not obviously worth trading that away — decide deliberately rather than
by reflex.

## 2. Two unreachable emoji fallbacks

`journal.KIND_EMOJI` is typed `dict[str, str]` and read via
`.get(entry.kind, "•")` and `.get(kind, "📖")`. Since
`test_every_entry_kind_has_an_emoji` asserts every member of
`EntryKind` has an entry, both defaults are dead for valid input.

Tightening the annotation to `dict[EntryKind, str]` and subscripting
directly would delete both branches. The trade is a `KeyError` instead
of a bullet if a kind is ever added without an emoji — though the test
catches that first, so the hard failure would never reach production.

## 3. ineligible_user_ids() is O(entries²)

`journal.ineligible_user_ids()` collects the distinct user ids, then
calls `is_ineligible()` per user, each of which re-`load()`s and
re-sorts the whole entry list.

**Do not optimize this yet.** The store is cache-backed and the guild
is ~100 members with a handful of records; there is no measured
problem, and `code-simplicity.md` forbids optimizing without evidence.
Recorded only so that whoever notices the shape later knows it was seen
and judged, not missed. If entry counts ever reach the thousands, a
single pass building a `user_id -> latest eligibility entry` map fixes
it.
