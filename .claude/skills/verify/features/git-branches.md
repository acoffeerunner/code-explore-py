# Git branch discovery

A client submits a repository URL and gets back its default branch and its branch list. A frontend uses this to fill a branch picker before it creates a repo. The route is `POST /git/branches` in `api/routes/git.py`. It runs `git ls-remote` through `GitService.get_branches`, after an SSRF check and host-allowlist validation (`utils/security.py`).

## Sub-features

- `branches-public` returns `default_branch` and `branches[]` for a public repo, with `is_public: true`.
- `branches-missing` handles a repo that doesn't exist or is private. It returns 200 with `is_public: false`, empty branches, and the git stderr in `error`.
- `branches-disallowed-host` rejects a host outside `ALLOWED_GIT_HOSTS` with 400 and the `SSRFError` message as `detail`.

## How to get to it (user POV)

- `POST /git/branches` with the body `{"url":"<https git url>"}` and a bearer token.

## Driving it with verify.sh

Preconditions: the instance is up, in either mode, and the machine can reach github.com.

- **Public repo.** Run `bash .claude/skills/verify/verify.sh call branches-ok POST /git/branches '{"url":"https://github.com/pallets/click"}'`. The response is HTTP 200 with `"default_branch":"main"`, a `branches` list that contains `"main"`, and `"is_public":true`.
- **Missing repo.** Run `... call branches-missing POST /git/branches '{"url":"https://github.com/pallets/this-repo-does-not-exist-xyz"}'`. The response is HTTP 200 with `"is_public":false`, `"branches":[]`, and an `error` that contains `Repository not found`.
- **Disallowed host.** Run `... call branches-ssrf POST /git/branches '{"url":"https://evil.example.com/a/b"}'`. The response is HTTP 400 with the detail `Host 'evil.example.com' is not in the allowed hosts list`.
- **Proof.** Run `grep 'path=/git/branches' .verify-runs/<run>/api.log`. Each call has a `Request completed` line with the matching `status_code` and the token's `user_id`.

## Gotchas

- `GitService.get_branches` must let `SSRFError` propagate. If it is wrapped in `GitError`, the route's catch-all turns it into a 500. `POST /repos` without a `branch` goes through the same path.
- An `http://` URL never reaches the service. The request model rejects it with 422.
- A missing repo is a 200 response. Assert on `is_public` and `error`, not on the HTTP status.
- When given a nonexistent repo, `git ls-remote` can prompt for credentials if a credential helper is configured. If a call hangs about 30 s and then times out, the git config caused it, not the API.
