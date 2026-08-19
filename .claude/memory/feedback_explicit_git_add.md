---
name: explicit-git-add-paths
description: Stage explicit file paths; never `git add <directory>` or `git add -A`
type: feedback
---

Always stage explicit file paths. Never `git add <directory>`, never `git add -A`.

**Why:** Unrelated work-in-progress routinely sits uncommitted in this tree for long stretches. On 2026-08-18 a `git add dragonpaw_bot/` swept two in-progress `plugins/subday/` files into an unrelated journal commit; undoing it meant rebuilding nine commits via cherry-pick. Because this repo pushes straight to `main`, there is no feature branch isolating the mistake.

**How to apply:** Name every file in `git add`. Run `git status` before committing and confirm each staged path belongs to the change at hand — anything you did not touch this session is the user's work, so leave it and ask before including it.
