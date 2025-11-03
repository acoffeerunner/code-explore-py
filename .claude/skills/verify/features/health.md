# Health and readiness

Orchestrators and the `keep-alive.yml` GitHub workflow poll these unauthenticated endpoints to learn whether the service is alive and whether its dependencies are reachable. The routes are defined in `api/routes/health.py`.

## Sub-features

- `health-live` makes `GET /health` return `{"status":"healthy"}`.
- `health-ready` makes `GET /health/ready` report `ready` or `degraded`, with per-dependency checks for `database`, `redis` and `pinecone`.
- `health-no-ratelimit` exempts `/health*` from the rate limiter.

## How to get to it (user POV)

- `curl http://127.0.0.1:8765/health`
- `curl http://127.0.0.1:8765/health/ready`. This is the URL `keep-alive.yml` pings.

## Driving it with verify.sh

Preconditions: the instance is up, in either mode.

- **Liveness.** Run `curl -s http://127.0.0.1:8765/health`. The response body is `{"status":"healthy"}`.
- **Readiness.** Run `curl -s http://127.0.0.1:8765/health/ready`.
  - Offline, the response is `{"status":"degraded","checks":{"database":"error","redis":"ok","pinecone":"error"}}`. Pinecone fails because the fake key gets `401 UNAUTHENTICATED` from `api.pinecone.io`.
  - Live with valid credentials, `status` is `ready`.
- **Dependency failure.** Run `docker stop ce-verify-redis`, then call `/health/ready` again. `redis` becomes `error`. Restart the container with `docker start ce-verify-redis`. If `docker stop` removed it (it was started with `--rm`), run `down`, then `up`.
- **Rate-limit exemption.** Run `curl -sI http://127.0.0.1:8765/health`. The response has no `x-ratelimit-*` headers.

## Gotchas

- The Pinecone check calls `describe_index` on `PINECONE_INDEX_NAME`, so `pinecone: ok` needs a valid key, network access, and an existing index. Each probe makes one Pinecone API call.
- `/health/ready` always returns HTTP 200, including when degraded. Assert on the body, not the status code.
