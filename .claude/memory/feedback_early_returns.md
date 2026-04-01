---
name: prefer-early-returns
description: Use early returns to minimize nesting of conditionals where possible
type: feedback
---

Use early returns to minimize nesting of conditionals where possible.

**Why:** Reduces indentation depth and makes code easier to follow.

**How to apply:** When writing conditional logic, check for the negative/error case first and return/continue early, rather than wrapping the happy path in a deep if/else block.
