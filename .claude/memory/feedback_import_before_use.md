---
name: Add uses before import (linter fires between edits)
description: When adding a new import to an existing file, ruff fires after each Edit and strips unused imports immediately
type: feedback
---

When adding a new import to an existing file, always add the code that uses the import **before** adding the import itself — or include both in the same Edit operation. Ruff fires between tool calls and strips any import that isn't yet referenced, so adding the import first and uses second means the import gets deleted before you can add the uses.

**Why:** The ruff linter hook fires after every Edit. An import with no references in the file at save time gets auto-removed as F401.

**How to apply:** When editing an existing file to add a new import + new code that uses it, either:
1. Add the using code first (in a separate Edit), then add the import — OR
2. Use a single Edit that includes both the import and the using code together (e.g. by anchoring old_string to span from the import block to the end of the file, or by using Write for a full rewrite when there are many changes).
