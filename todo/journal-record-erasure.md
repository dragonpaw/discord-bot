# Decide what happens when someone asks to be forgotten

The member journal is append-only by explicit design (2026-08-18):
nothing is ever deleted or edited, and corrections are follow-ups
appended to the entry they correct. That was the right call for a
moderation ledger — a retraction that erases the original destroys the
context a future reader needs.

It also means there is currently **no answer** to "please delete that".

## What is actually stored

`state/journal_{guild_id}.yaml` holds, per entry:

- The subject's display name as of the event
- A staff-written reason, in full
- For context-menu warnings: a verbatim snapshot of the member's
  message (`WarningDetail.evidence_text`), plus a jump link

So a member who asks for their record to be removed is asking about
staff prose about them *and* a copy of something they wrote. Deleting
the original Discord message does not touch the snapshot — that
persistence is the feature, which is exactly why it needs an answer.

## This is a decision, not a bug

Do not "fix" it by quietly adding a delete command. Append-only was
chosen deliberately and the whole design leans on it (there is no
tombstone, no `removed_by`, and `/journal view` assumes every entry it
loads is real). Adding erasure changes the data model's guarantees.

## Options, if the question ever comes up

1. **Do nothing.** A private server of ~100 people with a handful of
   records; the ledger is staff-only and never leaves the guild. This
   is the status quo and is defensible.
2. **Admin-only erasure of one member's entire record.** A single
   `/config journal forget <user>` that drops every entry for that
   user and logs the fact to the staff channel. Coarse on purpose —
   per-entry deletion would let a mod quietly edit history, which is
   the thing append-only exists to prevent.
3. **Redact only the snapshots.** Blank `evidence_text` across a
   member's entries but keep the staff-written record. Narrower, and
   targets the part that is genuinely the member's own words.

Option 3 is probably the best fit: it removes what the member owns and
keeps what staff wrote, which is the distinction that actually matters.

## Trigger

Revisit if a member asks, or if the server ever takes on obligations
that make "we keep it forever" the wrong default.
