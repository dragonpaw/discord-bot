---
name: No re-exports, keep DRY
description: Never re-export variables/functions through intermediate modules; import from the source. Keep code as DRY as possible.
type: feedback
---

Don't re-export variables, functions, or classes through intermediate modules. Always import directly from the defining module.

Keep code as DRY as possible — avoid duplication.

**Why:** User strongly dislikes indirection layers and redundant code. Re-exports obscure where things are defined and add maintenance burden.

**How to apply:** When adding new symbols, only define them in one place. When importing, import from the source module (e.g. `from dragonpaw_bot.context import ...` not `from dragonpaw_bot.utils import ...` for context-defined symbols). When you see existing re-exports, flag them or clean them up.
