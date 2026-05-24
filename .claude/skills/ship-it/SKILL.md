---
name: ship-it
description: Use when the user says "ship it", "deploy", "ship-it", or wants pending changes shipped end-to-end — commit pending changes, push to main, watch the GitHub Actions build, redeploy the bot on the NAS, and tail startup logs flagging errors. Project-specific to the discord-bot repo.
---

# /ship-it — Commit, build, deploy, verify

End-to-end shipping for the **dragonpaw discord-bot**. The repo's push-to-main triggers a CI build that pushes a Docker image to `ghcr.io/dragonpaw/discord-bot:latest`; the bot runs on the NAS as the Portainer stack `discord-bot`. CI does NOT auto-deploy — this skill closes the loop.

## When to use

- User says any of: "ship it", "/ship-it", "ship", "deploy", "deploy the bot", or asks to push and roll out pending work.
- Use after a feature, bugfix, or copy change is implemented and verified locally. NOT for in-progress work.

## When NOT to use

- Branch is not `main` (this project pushes straight to `main` — see CLAUDE.md "Git Workflow"). If user is on a branch, ask before merging/switching.
- Working tree is clean AND `main` is already up to date with `origin/main` — nothing to ship; tell the user.
- User explicitly wants only one of the steps (e.g. "just commit", "just deploy without committing").

## Preconditions

Verify before kicking off:

1. **On `main`**: `git rev-parse --abbrev-ref HEAD` returns `main`.
2. **Tests + lint + types pass locally** (the CI Dockerfile build doesn't run pytest):
   ```
   uv run ruff check dragonpaw_bot/ tests/
   uv run ty check dragonpaw_bot/
   uv run pytest
   ```
   If any fail, stop and report. Don't ship broken code.
3. **There is something to ship**: either uncommitted changes (`git status --short` non-empty) OR commits on `main` ahead of `origin/main` (`git log origin/main..main`).

## Steps

### 1. Commit (if there are uncommitted changes)

- Stage everything: `git add -A` (this project is single-author, no secret files typically lurk; still glance at `git status` first to catch `.env`, dumps, etc.).
- Write a real Conventional Commits message (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, etc., often scoped — `feat(intros): …`). Body explains *why*, wrapped at ~72 cols.
- Include the Claude co-author trailer per global commit guidance.
- Use a HEREDOC so multi-line bodies survive.

### 2. Push

```
git push origin main
```

If the push is rejected (someone else pushed concurrently), `git pull --rebase` then push again. Never force-push.

### 3. Watch the build

The workflow is `.github/workflows/build.yaml` ("Build and Deploy"). It only builds + pushes; there is no GH-side deploy.

```
sleep 5    # give Actions a moment to register the run
RUN_ID=$(gh run list --branch main --limit 1 --json databaseId,headSha -q ".[0].databaseId")
gh run watch "$RUN_ID" --exit-status
```

`gh run watch --exit-status` blocks until the run finishes and exits non-zero on failure. The Bash timeout should be ≥10 min (`timeout: 600000`). The build usually takes ~50s.

If the build fails: pull the failing job's logs with `gh run view "$RUN_ID" --log-failed`, surface the error, stop. Don't try to deploy a failed build.

### 4. Deploy on the NAS

The bot runs as Portainer stack id `5`, name `discord-bot`, single service `bot`. Compose lives at `/share/Docker/PortainerCE/data/compose/5/docker-compose.yml`. Env vars (`BOT_TOKEN`, `CLIENT_ID`) live in a sibling `stack.env` — Portainer manages them; the compose file declares the names without values.

**Critical gotcha**: `docker compose up -d` from the CLI does NOT pick up `stack.env` automatically. If you omit `--env-file stack.env`, the bot crash-loops with `KeyError: 'CLIENT_ID'`. Always pass `--env-file stack.env`.

```
ssh nas 'cd /share/Docker/PortainerCE/data/compose/5 && \
  sudo docker compose --env-file stack.env -p discord-bot pull && \
  sudo docker compose --env-file stack.env -p discord-bot up -d'
```

Note `-p discord-bot` so compose uses the same project name Portainer originally created the container with (`discord-bot-bot-1`) rather than `5_bot_1`.

This will show as drift in the Portainer UI but won't actually desync state — Portainer's own "redeploy" does the same `pull + up -d`. If you want zero drift, use the Portainer API instead (see `~/src/divoom-dashboard/Makefile` `deploy` target for the pattern with an API token).

**Don't read or print `stack.env` contents** — it holds the bot token. Pass it through with `--env-file`, never `cat` it.

### 5. Tail startup logs and triage

```
ssh nas 'timeout 60 sudo docker logs -f --since 70s discord-bot-bot-1 2>&1' > /tmp/ship-it-logs.txt
grep -iE 'warn|error|exception|traceback|critical|fail' /tmp/ship-it-logs.txt
```

Success signals to look for in the tail:
- `hikari.bot started successfully in approx N seconds`
- `Connected to Discord  user=Lizards, with Lazers#9577  build=<TAG>`
- `State loaded from disk, resuming services  guild=…`

**Benign warnings to ignore** (these fire on every boot — lightbulb scanning helper modules that don't expose loaders):
- `found no loaders in extension 'dragonpaw_bot.plugins.<x>.{models,state,config,constants,chart,commands}' - skipping`

**Real problems to surface**:
- Any `Traceback`.
- `KeyError`/`ValueError` at import time (usually env-var related — re-check you passed `--env-file stack.env`).
- Hikari `IDENTIFY` failures, gateway disconnects, or `unauthorized` (bad/expired token).
- The container restarting more than once in the 60s window (look for repeated `Bot starting up...` lines).

If the build TAG in the "Connected to Discord" line matches roughly the timestamp of the commit you just pushed, the new image is actually running.

### 6. Report back

One-paragraph summary: commit SHA, build status, deploy result, startup time, and any non-benign warnings (or "clean startup"). Don't include the bot token, raw stack.env contents, or full noisy log dumps.

## Quick reference

| Thing | Value |
|---|---|
| Repo | `dragonpaw/discord-bot` |
| Branch model | push direct to `main` |
| Image | `ghcr.io/dragonpaw/discord-bot:latest` |
| CI workflow | `.github/workflows/build.yaml` |
| NAS host | `nas` (ssh config) — see `~/.claude/skills/asustor-nas/SKILL.md` |
| Portainer stack | id `5`, name `discord-bot` |
| Container name | `discord-bot-bot-1` |
| Compose path | `/share/Docker/PortainerCE/data/compose/5/docker-compose.yml` |
| Env file | `/share/Docker/PortainerCE/data/compose/5/stack.env` (do not print) |
| Service | `bot` |
| State volume | `bot-state` → `/app/state` in container |
