---
name: ship-it
description: Use when the user says "ship it", "deploy", "ship-it", or wants pending changes landed and rolled out end-to-end. Project-specific to the discord-bot repo.
---

# /ship-it — Simplify, review, validate, commit, push, build, deploy, verify

End-to-end shipping for the **dragonpaw discord-bot**:
**simplify → review → validate → commit → push → watch CI → redeploy → verify**.

The repo's push-to-main triggers a CI build that pushes a Docker image to `ghcr.io/dragonpaw/discord-bot:latest`; the bot runs on the NAS as the Portainer stack `discord-bot` (id `28`, endpoint `6`) on the Portainer hub on plugger, container name `discord-bot`. CI does NOT auto-deploy — this skill closes the loop.

## When to use

- User says any of: "ship it", "/ship-it", "ship", "deploy", "deploy the bot", or asks to push and roll out pending work.
- Use after a feature, bugfix, or copy change is implemented and verified locally. NOT for in-progress work.

## When NOT to use

- Branch is not `main` (this project pushes straight to `main` — see CLAUDE.md "Git Workflow"). If user is on a branch, ask before merging/switching.
- Working tree is clean AND `main` is already up to date with `origin/main` — nothing to ship; tell the user.
- User explicitly wants only one of the steps (e.g. "just commit", "just deploy without committing").

## Shared conventions

### Secret-pattern skip list

Used by the simplifier (step 2) and the commit step (step 5). Skip files matching any of: `.env*`, `*credentials*`, `*secret*`, `*.key`, `*.pem`, `stack.env`.

### Changed-files helper

Steps 2, 3, and 5 need the deduplicated list of files modified vs `origin/main`. Four slices must be unioned — drop one and a file class is silently missed:

```bash
changed_files() {
    {
        git diff --name-only origin/main...HEAD   # committed ahead of origin
        git diff --name-only --cached             # staged
        git diff --name-only                      # unstaged
        git ls-files --others --exclude-standard  # untracked
    } | sort -u
}
```

Filter the list against the secret-pattern skip list before passing it to subagents.

### Doc-only diffs and instruction docs

A diff with no `.py` changes skips the simplifier (nothing for a code simplifier to act on) — but **not the review** when it touches instruction docs: `CLAUDE.md`, any `plugins/*/CLAUDE.md`, `code-simplicity.md`, or `.claude/skills/`. Those documents are agent-executed logic — a wrong line misroutes every future run — so review them regardless. A plain README/docs typo may skip review only via `--skip-review`.

### Steps 2 and 3 dispatch their own agents

Steps 2 and 3 mean *separate agents*, not a second pass by the agent that wrote the code. The point is independence: an author grading their own diff re-derives their earlier reasoning instead of attacking it, and the checks that matter most (the Three Flaws, untested paths, over-genericism) are exactly the ones an author is blindest to.

**Invoking `/ship-it` is a request for these agents.** Only these conditions justify the inline path: the agent genuinely can't be spawned, the agent fails (see below), the user explicitly said to run it inline, or (step 2 only) the diff is doc-only. Nothing else. "The change is small", "I already know what it'll say", "it'd be faster", and "I reviewed as I wrote it" are not reasons.

**Subagent failure handling:** if a simplifier or reviewer agent is unavailable, errors, or returns nothing, surface the failure and run that step inline with the identical scope and rubric. Whenever a step runs inline, **say so in the final summary** — name the step, the reason, and that the pass was not independent. A reader must never have to guess whether "review passed" meant an independent agent or the author's own re-read.

### Unattended mode

When running without a user to answer (auto mode, scheduled runs): Warn-tier review findings → log and proceed; Block-tier findings always stop. Any other "ask the user" branch takes the documented safe default, or stops at a resumable checkpoint if human judgment is required.

## Steps

### 1. Pre-flight

```bash
git fetch origin main --quiet
```

- **On `main`**: `git rev-parse --abbrev-ref HEAD` returns `main`. If not, stop and ask.
- **Something to ship**: uncommitted changes (`git status --short` non-empty) OR commits ahead (`git rev-list --count origin/main..main` > 0). Neither → exit cleanly: "Nothing to ship."
- Commits ahead but clean tree: still run steps 2–4 against the committed diff; step 5 simply won't create a new commit.

### 2. Simplify

Skip if `--skip-simplify`, or if the diff is doc-only (see "Doc-only diffs").

**Dispatch the `code-simplifier:code-simplifier` agent** — see "Steps 2 and 3 dispatch their own agents". Keep the roles straight: the agent is the **engine** that proposes edits; `code-simplicity.md` is the **rubric** that decides what counts as a real simplification and where to stop.

Snapshot first, so there is a copy to compare against and restore from:

```bash
git diff > <scratchpad>/pre-simplify.patch
```

Prompt the agent with concrete inputs, not "look at git status":

- **Scope**: only the secret-filtered `changed_files` list; never unrelated files or stable code already shipped.
- **Standards**: `code-simplicity.md` + repo CLAUDE.md conventions — especially the Three Flaws (speculative code, rigid non-DRY code, over-genericism). No abstraction the diff doesn't need.
- **Behaviour preservation**: no semantic changes unless fixing an obvious bug; call out and justify any in its summary.
- **Apply gate** (per `code-simplicity.md` § "What to refuse"): trivial local cleanups (inline a single-use variable, delete dead code, DRY a duplicated literal) apply directly. Structural changes (merging/splitting functions, abstraction or signature changes) are surfaced for confirmation, not silently applied.
- **Edits go through edit tools, never through git.** `git checkout` / `restore` / `reset` / `stash` / `clean` / `add` are forbidden, as is committing. The change being shipped is usually still uncommitted, so a git-level undo eats the operator's work with no copy to restore from. Reading git (`diff`, `log`, `status`, `show`) is unrestricted.

After it returns: check scope wasn't exceeded, then diff the snapshot against the current `git diff` — only the comparison catches your own work being reverted out from under you (`--stat` alone is not enough; a partial revert leaves plausible-looking counts). Then `uv run ruff check dragonpaw_bot/ tests/` — if lint fails, hand the output back or fix; don't review broken code.

### 3. Review

Skip if `--skip-review` (log: "Skipping code review (not recommended)" and continue).

**Run the `code-review` skill** on the pending diff vs `origin/main`; if unavailable, dispatch a `feature-dev:code-reviewer` agent with the changed-file list and the rubric below. The independence *is* the check.

**Any dispatched reviewer is strictly read-only — say so in its prompt every time.** The working tree is usually the only copy of the change. Forbid explicitly: editing/creating/deleting any file, and `git checkout` / `restore` / `stash` / `reset` / `clean` / `add` / commit — none of those "edit a file", so a prompt that only says "don't edit files" permits every one of them, and each destroys uncommitted work. Reading files and read-only git inspection are unrestricted. If a reviewer reports having touched the tree anyway, treat the verdict as untrusted: re-check `git diff` against what you last saw before committing, and re-run the review if anything moved.

Point the review at `code-simplicity.md` and repo CLAUDE.md, and have it audit:

- **The Three Flaws** (`code-simplicity.md`): speculative code, rigid non-DRY code, over-genericism (e.g. a new abstraction with one caller). Any clear violation is **Block** severity.
- **The per-change checklist** (`code-simplicity.md` § "A checklist for every change"): failures are **Warn** unless severe (untested new code path → Block).
- **Repo conventions** ruff/ty can't catch: interaction-respond-before-slow-work, dragon persona in user-facing copy and `gc.log()`, structlog conventions, plugin CLAUDE.md updated to match the change.

Collapse findings into tiers:

- **Block** — critical / must-fix, or any Three-Flaws violation. Show file + line and stop; fix and re-run ship-it.
- **Warn** — should-fix / checklist gaps. Ask "proceed? (yes/no)"; unattended: log and proceed.
- **Info** — nits. Final summary only; don't prompt.

### 4. Validate

The CI Dockerfile build doesn't run pytest, so the local gate is the only test gate:

```bash
uv run ruff check dragonpaw_bot/ tests/
uv run ty check dragonpaw_bot/
uv run pytest
```

Any check fails → stop and report. Don't ship broken code.

### 5. Commit (if there are uncommitted changes)

- Glance at `git status --short` for secret-pattern matches or stray dumps, then stage: `git add -A` is acceptable in this single-author repo once the glance is clean.
- Write a real Conventional Commits message (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, often scoped — `feat(intros): …`). Body explains *why*, wrapped at ~72 cols.
- Include the Claude co-author trailer per global commit guidance.
- Use a HEREDOC so multi-line bodies survive.
- If the commit fails (hook, etc.), fix and create a *new* commit — never `--amend`: the hook failure means no new commit exists, so amend would rewrite the previous, unrelated one.

### 6. Push

```bash
git push origin main
```

If the push is rejected (someone else pushed concurrently), `git pull --rebase`, re-run step 4 (an upstream change can break this branch without a textual conflict), then push again. Never force-push. Rebase conflicts: stop and resolve with the user; never auto-resolve.

### 7. Watch the build

The workflow is `.github/workflows/build.yaml` ("Build and Deploy"). It only builds + pushes the image; there is no GH-side deploy.

```bash
sleep 5    # give Actions a moment to register the run
RUN_ID=$(gh run list --branch main --limit 1 --json databaseId,headSha -q ".[0].databaseId")
gh run watch "$RUN_ID" --exit-status
```

`gh run watch --exit-status` blocks until the run finishes and exits non-zero on failure. The Bash timeout should be ≥10 min (`timeout: 600000`). The build usually takes ~50s.

If the build fails: pull the failing job's logs with `gh run view "$RUN_ID" --log-failed`, surface the error, stop. Don't deploy a failed build.

### 8. Deploy on the NAS (Portainer hub API)

The bot runs as the standalone (non-git) compose stack **`discord-bot`** (id `28`) on **endpoint 6** (`nas`) of the Portainer hub on **plugger** (`http://10.0.2.203:19900`). The single service `bot` runs as container **`discord-bot`** (host network). See `~/.claude/skills/asustor-nas/SKILL.md` for the hub/endpoint model. (History: re-adopted onto ep6 on 2026-07-04 from the retired ep3 stack id 5; the old on-box `docker compose --env-file stack.env` path is dead — ep3's endpoint no longer exists.)

Credentials come from `~/.config/fish/conf.d/nas.fish`: `$PORTAINER_URL`, `$PORTAINER_TOKEN` (`ptr_…`), `$PORTAINER_ENDPOINT` (`6` = nas). **The token is a secret — never print it.** The hub is LAN-direct from the workstation (no ssh); off-LAN, tunnel with `ssh -L 19900:10.0.2.203:19900 …`.

Redeploy = re-pull `:latest` and recreate the container, round-tripping the stack's stored env (`BOT_TOKEN`/`CLIENT_ID`) so you never handle the token. A single `PUT …?endpointId=6` with `pullImage:true` does it. Write this to scratchpad and run it:

```python
# scratchpad/redeploy.py — needs PORTAINER_URL / PORTAINER_TOKEN in env
import json, os, urllib.request
URL, TOK = os.environ["PORTAINER_URL"], os.environ["PORTAINER_TOKEN"]
H = {"X-API-Key": TOK, "Content-Type": "application/json"}
def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    return urllib.request.urlopen(urllib.request.Request(URL + path, data=data, headers=H, method=method))
st = next(s for s in json.load(call("GET", "/api/stacks"))
          if s["Name"] == "discord-bot" and s["EndpointId"] == 6)   # look up by name, not hardcoded id
sid = st["Id"]
compose = json.load(call("GET", f"/api/stacks/{sid}/file"))["StackFileContent"]
env = [{"name": e["name"], "value": e["value"]} for e in st["Env"]]  # round-trips BOT_TOKEN/CLIENT_ID
body = {"stackFileContent": compose, "env": env, "prune": False, "pullImage": True}
print("redeploy status:", call("PUT", f"/api/stacks/{sid}?endpointId=6", body).status, "stack", sid)
```

```
source ~/.config/fish/conf.d/nas.fish   # or export PORTAINER_URL / PORTAINER_TOKEN
python3 scratchpad/redeploy.py
```

`pullImage: true` re-pulls `ghcr.io/dragonpaw/discord-bot:latest` before recreating `discord-bot` in place; the named volume `discord-bot_bot-state` (guild/config state) is preserved.

**If the stack is missing** (someone deleted it): recreate with `POST /api/stacks/create/standalone/string?endpointId=6`, name `discord-bot`, `stackFileContent` from `~/src/discord-bot/docker-compose.yml`, and env `BOT_TOKEN`/`CLIENT_ID`. The values are recoverable from the stale ep3 file: `ssh nas 'cat /share/Docker/PortainerCE/data/compose/5/stack.env'` — pipe it into the create, **never print it**. Keep `TEST_GUILDS` unset in prod (global command registration only).

**Note:** these Portainer-API writes are production deploys — the auto-mode classifier may prompt for approval even though `ssh nas` itself is pre-authorized via the asustor-nas standing grant.

### 9. Tail startup logs and triage (Portainer API)

Fetch the container's logs from the hub (no ssh). Container name is **`discord-bot`**:

```
source ~/.config/fish/conf.d/nas.fish
curl -s -H "X-API-Key: $PORTAINER_TOKEN" \
  "$PORTAINER_URL/api/endpoints/6/docker/containers/discord-bot/logs?stdout=true&stderr=true&timestamps=true&tail=200" \
  | LC_ALL=C sed -E 's/^.{8}//' | sed 's/\x1b\[[0-9;]*m//g' > /tmp/ship-it-logs.txt
grep -iE 'warn|error|exception|traceback|critical|fail' /tmp/ship-it-logs.txt
```

Docker's log stream is multiplexed — `sed 's/^.{8}//'` strips the 8-byte frame header; bot logs are ANSI-colorized — the second `sed` strips color (both per the asustor-nas skill). For byte-exact logs, use on-box `ssh nas 'sudo docker logs -t --since 70s discord-bot'`.

Success signals to look for in the tail:
- `hikari.bot started successfully in approx N seconds`
- `Connected to Discord  user=Lizards, with Lazers#9577  build=<TAG>`
- `State loaded from disk, resuming services  guild=…`

**Benign warnings to ignore** (these fire on every boot — lightbulb scanning helper modules that don't expose loaders):
- `found no loaders in extension 'dragonpaw_bot.plugins.<x>.{models,state,config,constants,chart,commands}' - skipping`

**Real problems to surface**:
- Any `Traceback`.
- `KeyError`/`ValueError` at import time (usually env-var related — check the stack's stored env still carries `BOT_TOKEN` and `CLIENT_ID`; the `PUT` round-trips them, but a bad recreate can drop them).
- Hikari `IDENTIFY` failures, gateway disconnects, or `unauthorized` (bad/expired token).
- The container restart-looping. Confirm it's stable via the container's state (want `running`, `RestartCount` 0):
  ```
  curl -s -H "X-API-Key: $PORTAINER_TOKEN" "$PORTAINER_URL/api/endpoints/6/docker/containers/discord-bot/json" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); s=d["State"]; print("status:",s["Status"],"restarts:",d["RestartCount"],"started:",s["StartedAt"])'
  ```

If the build TAG in the "Connected to Discord" line matches roughly the timestamp of the commit you just pushed, the new image is actually running.

### 10. Report back

One-paragraph summary: commit SHA, simplify/review outcomes (including any step that ran inline instead of via an independent agent, and why), build status, deploy result, startup time, and any non-benign warnings (or "clean startup"). Don't include the bot token, raw stack.env contents, or full noisy log dumps.

## Arguments

- `--skip-simplify` — skip step 2.
- `--skip-review` — skip step 3. Reserve for genuinely trivial changes (doc-only typo, comment edit) or changes already reviewed by other means.

## Quick reference

| Thing | Value |
|---|---|
| Repo | `dragonpaw/discord-bot` |
| Branch model | push direct to `main` |
| Simplifier | `code-simplifier:code-simplifier` agent, rubric `code-simplicity.md` |
| Reviewer | `code-review` skill (fallback: `feature-dev:code-reviewer` agent, read-only) |
| Local gate | `uv run ruff check` + `uv run ty check dragonpaw_bot/` + `uv run pytest` |
| Image | `ghcr.io/dragonpaw/discord-bot:latest` |
| CI workflow | `.github/workflows/build.yaml` |
| NAS host | `nas` (ssh config) — see `~/.claude/skills/asustor-nas/SKILL.md` |
| Portainer hub | plugger `http://10.0.2.203:19900`, endpoint `6` = nas |
| Portainer creds | `~/.config/fish/conf.d/nas.fish` → `$PORTAINER_URL`, `$PORTAINER_TOKEN` (secret) |
| Portainer stack | name `discord-bot`, id `28` (look up by name + `EndpointId==6`) |
| Container name | `discord-bot` |
| Deploy | `PUT /api/stacks/{id}?endpointId=6` with `pullImage:true` (see step 8) |
| Compose (source of truth) | `~/src/discord-bot/docker-compose.yml` |
| Env (`BOT_TOKEN`/`CLIENT_ID`) | stored in the Portainer stack; stale ep3 copy at `/share/Docker/PortainerCE/data/compose/5/stack.env` (recovery only, do not print) |
| Service | `bot` |
| State volume | `discord-bot_bot-state` → `/app/state` in container |
