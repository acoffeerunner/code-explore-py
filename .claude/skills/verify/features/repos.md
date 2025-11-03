# Repo indexing lifecycle

A user registers a Git repo and branch. The API stores a `repos` row and enqueues `index_repo_task` on ARQ. The worker clones, chunks, embeds and upserts vectors into a versioned Pinecone namespace. Status moves from `pending` through `cloning` and `indexing` to `ready` or `failed`. The routes are in `api/routes/repos.py`.

## Sub-features

- `repo-create` handles `POST /repos` with `{"url", "branch"?, "webhook_url"?}`. It returns 201 with `status: pending` and uses the default branch when `branch` is omitted. Sending the same url and branch again returns 409.
- `repo-status` handles `GET /repos/{id}/status`, which returns `status`, `progress_pct`, `file_count`, `chunk_count`, `error_message` and `indexed_at`.
- `repo-list-get` handles `GET /repos` and `GET /repos/{id}`. Both are scoped to the token's user.
- `repo-reindex` handles `POST /repos/{id}/reindex` with `{"force": bool}`.
- `repo-delete` handles `DELETE /repos/{id}`. It returns 204 and removes the row and the vectors.
- `repo-webhook` sends an HMAC-signed callback to `webhook_url` on a status change. The `webhook_secret` is returned once, at create time.

## How to get to it (user POV)

- REST calls with a bearer token, in this order: create, poll status, list or get, then reindex or delete.

## Driving it with verify.sh

Preconditions:
- Live mode (`up` without `--offline`).
- A real `.env` exists.
- `VERIFY_USER_ID` is exported and set to an existing `auth.users.id`.
- `doctor` reports `ready`.

- **Create.** Run `bash $V call repo-create POST /repos '{"url":"https://github.com/pallets/click","branch":"main"}'`. The response is HTTP 201 with `"status":"pending"` and an `id`. Save that id as `RID`.
- **Worker picked it up.** Run `grep -n index_repo_task .verify-runs/<run>/worker.log`. The output shows the job starting for `RID`.
- **Poll.** Repeat `bash $V call repo-status-N GET /repos/$RID/status` until `status` is `ready` or `failed`. A `ready` response has `chunk_count > 0` and a non-null `indexed_at`.
- **Duplicate.** Repeat the create call. The response is HTTP 409.
- **List.** Run `bash $V call repo-list GET /repos`. The response contains `RID`.
- **Delete.** Run `bash $V call repo-delete DELETE /repos/$RID`, which returns HTTP 204. Then `GET /repos/$RID` returns 404.

## Gotchas

- Offline, every `/repos` call returns 500 (database unreachable). Report the feature as unverified, not as failed.
- If `VERIFY_USER_ID` isn't a real user, create fails with a 500 that comes from the foreign-key violation on `repos.user_id`.
- Indexing spends OpenAI embedding tokens and writes Pinecone vectors. Use a small repo and always delete it.
- Clones go to `.verify-runs/<run>/clones` through `CLONE_BASE_PATH`. `down` removes that directory.
