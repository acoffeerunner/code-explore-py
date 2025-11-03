# Code Explorer verification map

This directory is the maintained source for verifying Code Explorer's user-facing API behavior. Read this index before you drive the service. Then follow the matching feature file as the recipe.

| Feature | File | Mode needed |
|---|---|---|
| Health and readiness | [health.md](health.md) | offline |
| Auth and rate limiting | [auth-rate-limit.md](auth-rate-limit.md) | offline |
| Git branch discovery | [git-branches.md](git-branches.md) | offline |
| Repo indexing lifecycle | [repos.md](repos.md) | live |
| RAG chat and history | [chat.md](chat.md) | live, needs an indexed repo |

## Baseline preconditions

- Launch with `bash .claude/skills/verify/verify.sh up [--offline]`. The API runs on `127.0.0.1:8765` and Redis in the `ce-verify-redis` container on `:6390`.
- `bash .claude/skills/verify/verify.sh doctor` exits 0.
- Live mode also needs a real `.env` and `VERIFY_USER_ID` set to an existing Supabase `auth.users.id`. `repos.user_id` has a foreign key to that table.
- Never drive an instance this run didn't start.

## Driving conventions

- Send authenticated requests with `verify.sh call <name> <METHOD> <path> '<json>'`. Give each call a unique `<name>`, because the name becomes the evidence filename.
- For unauthenticated checks, use plain `curl` against `http://127.0.0.1:8765`.
- Treat paths and JSON as literal values.

## Proof and skip reporting

- A proof includes the `call` artifacts, the matching `Request completed` line in `api.log`, and a second read of any side effect.
- If a live-only feature runs without a live `.env`, report it as unverified and name the missing precondition.
- Don't report an offline 500 on `/repos` or `/chat` as a verified failure.

## Feature entry contract

Each feature file has an H1 title, one paragraph, and then these H2 sections in order: `Sub-features`, `How to get to it (user POV)`, `Driving it with verify.sh`, `Gotchas`.
