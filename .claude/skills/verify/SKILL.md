---
name: verify
description: Launch and drive the Code Explorer FastAPI service (HTTP/JSON API + ARQ worker) the way a client does, and capture request/response evidence. Use to prove a change to health, git branch discovery, repo indexing, or RAG chat works against a real running instance, not just unit tests.
---

# Verify Code Explorer

The only user surface is the HTTP API (`src/code_explorer/main.py`, routers under `src/code_explorer/api/routes/`). Clients send JSON with a Supabase JWT bearer token. Indexing runs in a separate ARQ worker (`python -m code_explorer.workers.worker`) that pulls jobs from Redis. No UI is shipped. `/docs` serves Swagger.

Everything goes through one helper, `.claude/skills/verify/verify.sh`. Run it from the repo root with Git Bash (`bash .claude/skills/verify/verify.sh <cmd>`). The feature recipes are in `features/`. Start with `features/README.md`.

## Two modes: pick before launching

| Mode | Needs | Proves |
|---|---|---|
| `--offline` | Docker, `.venv` | health, auth, rate limiting, `POST /git/branches` (real `git ls-remote` to GitHub) |
| live (default) | the above plus a real `.env` (Supabase, Pinecone, OpenAI) and `VERIFY_USER_ID` set to a real `auth.users.id` | everything, including repos, indexing and chat |

Offline mode uses dummy credentials and runs uvicorn from the evidence directory, so the user's `.env` never loads. In offline mode, `/repos*` and `/chat*` fail with 500 because the database is unreachable. That failure is expected; don't report it as a regression. If a feature needs live mode and there is no `.env`, report it as unverified and give the reason. Don't substitute an offline call.

## Setup (once per checkout)

```bash
uv venv .venv && uv pip install --python .venv/Scripts/python.exe -e ".[dev]"   # Linux/mac: .venv/bin/python
```

Docker Desktop must be running (`docker info`). On Windows, start it with `"/c/Program Files/Docker/Docker/Docker Desktop.exe" &` and wait for `docker info` to succeed.

## Launch

```bash
bash .claude/skills/verify/verify.sh up --offline    # or: up   (live, also starts the worker)
```

This starts:
- Redis in the `ce-verify-redis` container on `127.0.0.1:6390`. It is private to this run, so jobs never reach the user's queue.
- The API on `127.0.0.1:8765` (override with `VERIFY_PORT`).
- In live mode, the worker, pointed at the same Redis.

The instance is ready when the helper prints `ready: http://127.0.0.1:8765 mode=... evidence=<dir>`. It waits until `/health` answers. `up` refuses to start if `.verify-runs/state/` exists or the port is busy. Never drive or kill an instance you didn't start.

## Doctor

```bash
bash .claude/skills/verify/verify.sh doctor
```

This check is read-only. It confirms that the recorded API PID owns the port and that `/health` returns `{"status":"healthy"}`. It also prints `/health/ready` and pings our Redis. A non-zero exit means don't drive the instance: run `down`, then `up`. In offline mode, `/health/ready` reports `degraded` with `database: error`, which is the expected result. Offline, `pinecone` also reports `error`, because the readiness check calls `describe_index` with the fake key.

## Drive

```bash
V=.claude/skills/verify/verify.sh
bash $V call <name> <METHOD> <path> ['<json body>']    # authenticated; saves evidence
bash $V token                                          # raw JWT if you need curl yourself
```

`call` mints an HS256 JWT (`aud=authenticated`, `sub=$VERIFY_USER_ID` or a fixed test UUID) and signs it with the mode's `SUPABASE_JWT_SECRET`. This is the real verification path in `api/middleware/auth.py`: the JWKS fetch fails, and auth falls back to HS256. Each call writes `<name>.request`, `<name>.status`, `<name>.headers` and `<name>.body` into the run's evidence directory and prints the status and body. To test unauthenticated behavior, use plain `curl` without the header.

Example (offline, verified):
```bash
bash $V call branches-ok POST /git/branches '{"url":"https://github.com/pallets/click"}'
# HTTP 200 {"url":...,"default_branch":"main","branches":["main",...],"is_public":true,"error":null}
```

Use the route paths in `features/*.md`.

## Evidence

Evidence goes in `.verify-runs/<YYYYmmdd-HHMMSS>-<mode>/`, which is git-ignored and survives `down`. It contains `api.log` (console-format structlog with `request_id`, `status_code` and `user_id` per request), `worker.log` in live mode, and the `call` artifacts.

A proof must meet these standards:
- Exercise the real client path: HTTP plus bearer token through the full middleware stack. Don't call service classes or set internal state directly.
- Capture the action and the resulting state: the `call` artifacts, plus the matching `Request completed ... status_code=` line in `api.log`.
- Verify side effects through a second read. Repo creation needs a later `GET /repos/{id}/status` and the worker log showing the job. Rate limiting needs `docker exec ce-verify-redis redis-cli --scan --pattern 'ratelimit:*'`. Chat needs `GET /chat/history/{repo_id}`.
- Don't mock anything. Offline mode uses real Redis, real git and real JWT checks. It can't reach the database, Pinecone or OpenAI, so features that need them stay unverified offline.
- Indexing and chat in live mode write real Pinecone vectors and spend OpenAI tokens. Use a small public repo (`https://github.com/pallets/click` or smaller), and `DELETE /repos/{id}` afterwards.

## Cleanup

```bash
bash .claude/skills/verify/verify.sh down
```

`down` stops only the recorded API and worker PIDs (`taskkill /T`) and the `ce-verify-redis` container. It then removes `.verify-runs/state/` and the run's `clones/` scratch directory. It never kills by process name, and it keeps the evidence directory. Rows and vectors created in live mode are not removed. Delete them through the API (`DELETE /repos/{id}`, `DELETE /chat/history/{repo_id}`) before `down`. Run `down` after every failed attempt too.

## Helper

The only helper is `.claude/skills/verify/verify.sh`. Its subcommands are `up [--offline]`, `doctor`, `token`, `call NAME METHOD PATH [JSON]` and `down`. Run it with no arguments for usage. It is written for Git Bash on Windows (`netstat -ano`, `taskkill`, `.venv/Scripts/python.exe`). The PID lookup and kill lines need a POSIX equivalent before it runs on Linux or macOS.
