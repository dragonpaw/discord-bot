---
name: delete-finished-specs-plans
description: Finished specs, plans, and todo documents are deleted, never committed
type: feedback
---

Finished specs, plans, and todo-list documents are deleted once implemented — never committed as artifacts.

**Why:** The code, its commit message, and the plugin's `CLAUDE.md` are the durable record. A spec that survives implementation becomes a second source of truth that drifts from the code and misleads the next reader.

**How to apply:** Superpowers skills (`brainstorming`, `writing-plans`) tell you to write specs/plans to `docs/superpowers/` and commit them. Write them — they earn their keep while working — but do not commit them, and delete them once the work lands. Fold anything worth keeping into the plugin's `CLAUDE.md` or the commit message. Note this is the opposite of `todo/`, which is tracked in git: `todo/` holds work *not yet started*; specs and plans describe work already done.
