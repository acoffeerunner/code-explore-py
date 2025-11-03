# Auth and rate limiting

Every non-health route needs a Supabase JWT. `api/middleware/auth.py` tries ES256 through JWKS first, then falls back to HS256 with `SUPABASE_JWT_SECRET`. Every non-health request also passes a Redis sliding-window limiter, per IP and per JWT `sub`, in `api/middleware/rate_limit.py`.

## Sub-features

- `auth-missing` rejects a request that has no bearer token with 401.
- `auth-invalid` rejects a bad signature, an expired token, or a wrong `aud` with 401.
- `ratelimit-headers` adds `x-ratelimit-limit`, `x-ratelimit-remaining` and `x-ratelimit-reset` to every rate-limited response.
- `ratelimit-exceeded` rejects a request once a bucket exceeds `RATE_LIMIT_PER_USER` (default 100 per 60 s) or `RATE_LIMIT_PER_IP`. It returns 429 with `{"detail":"Rate limit exceeded. Please slow down."}` and `Retry-After`.

## How to get to it (user POV)

- Any call to `/git/*`, `/repos*` or `/chat*`, with or without an `Authorization: Bearer <jwt>` header.

## Driving it with verify.sh

Preconditions: the instance is up, in either mode.

- **No token.** Run `curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{"url":"https://github.com/pallets/click"}' http://127.0.0.1:8765/git/branches`. The output is `401`.
- **Bad token.** Run the same request with `-H 'Authorization: Bearer abc.def.ghi'`. The response is 401 `{"detail":"Invalid token"}`.
- **Good token and headers.** Run `bash .claude/skills/verify/verify.sh call rl-ok POST /git/branches '{"url":"https://github.com/pallets/click"}'`. Then run `grep -i ratelimit .verify-runs/<run>/rl-ok.headers`. The output shows `x-ratelimit-limit: 100` and a decrementing `remaining` value.
- **Redis side effect.** Run `docker exec ce-verify-redis redis-cli --scan --pattern 'ratelimit:*'`. The output lists `ratelimit:user:<sub>` and `ratelimit:ip:127.0.0.1`.
- **Limit exceeded.** Run `down`, then `export RATE_LIMIT_PER_USER=3` and `up --offline`. Send four `call rl-N POST /git/branches ...` requests. Calls 1 to 3 return HTTP 200. Call 4 returns HTTP 429 with a `retry-after` header and `x-ratelimit-remaining: 0`. `api.log` shows `User rate limit exceeded` and no `Traceback`.

## Gotchas

- With Redis down, every non-health route returns 500. The limiter doesn't fail open.
- The per-user bucket is keyed on the unverified `sub`. A forged token still consumes that user's bucket before auth rejects it.
- `RateLimitMiddleware` must return its 429 response, not raise `HTTPException`. FastAPI's exception handlers don't run inside a `BaseHTTPMiddleware`, so a raise becomes a 500. A 500 on call 4 means this regressed.
- The bad-token check (`abc.def.ghi`) doesn't consume a user bucket, because an undecodable token has no `sub`.
