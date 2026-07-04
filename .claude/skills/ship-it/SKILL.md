---
name: ship-it
description: Use when the user says "ship it", "deploy", "ship-it", or wants pending changes shipped end-to-end — commit pending changes, push to main, watch the GitHub Actions build, redeploy the bot on the NAS, and tail startup logs flagging errors. Project-specific to the discord-bot repo.
---

# /ship-it — Commit, build, deploy, verify

End-to-end shipping for the **dragonpaw discord-bot**. The repo's push-to-main triggers a CI build that pushes a Docker image to `ghcr.io/dragonpaw/discord-bot:latest`; the bot runs on the NAS as the Portainer stack `discord-bot` (id `28`, endpoint `6`) on the Portainer hub on plugger, container name `discord-bot`. CI does NOT auto-deploy — this skill closes the loop.

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

### 4. Deploy on the NAS (Portainer hub API)

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

### 5. Tail startup logs and triage (Portainer API)

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
| Portainer hub | plugger `http://10.0.2.203:19900`, endpoint `6` = nas |
| Portainer creds | `~/.config/fish/conf.d/nas.fish` → `$PORTAINER_URL`, `$PORTAINER_TOKEN` (secret) |
| Portainer stack | name `discord-bot`, id `28` (look up by name + `EndpointId==6`) |
| Container name | `discord-bot` |
| Deploy | `PUT /api/stacks/{id}?endpointId=6` with `pullImage:true` (see step 4) |
| Compose (source of truth) | `~/src/discord-bot/docker-compose.yml` |
| Env (`BOT_TOKEN`/`CLIENT_ID`) | stored in the Portainer stack; stale ep3 copy at `/share/Docker/PortainerCE/data/compose/5/stack.env` (recovery only, do not print) |
| Service | `bot` |
| State volume | `discord-bot_bot-state` → `/app/state` in container |
