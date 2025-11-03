# RAG chat and history

A user asks a natural-language question about an indexed repo. The pipeline does history rewrite, HyDE, hybrid Pinecone search, a similarity threshold with a soft floor, an LLM rerank, and an OpenAI answer with cited sources. Turns are stored per repo. The routes are in `api/routes/chat.py`.

## Sub-features

- `chat-answer` handles `POST /chat` with `{"repo_id","question","model"?,"top_k"?,"history"?}`. It returns an answer with `sources`.
- `chat-stream` handles `POST /chat/stream`. It sends SSE `event: token` frames, then a final `event: done` frame with `sources` and `model`.
- `chat-no-results` returns a fixed "I couldn't find any relevant code..." message with empty sources when retrieval finds nothing.
- `chat-history` handles `GET /chat/history/{repo_id}?limit&offset`, which returns messages oldest first. `DELETE /chat/history/{repo_id}` returns 204.
- `chat-ownership` returns 404 for another user's repo.

## How to get to it (user POV)

- Call `POST /chat` or `POST /chat/stream` with a bearer token after the repo reaches `ready`.

## Driving it with verify.sh

Preconditions: live mode, and repo `RID` is `ready` (see [repos.md](repos.md)).

- **Ask.** Run `bash $V call chat-1 POST /chat '{"repo_id":"'$RID'","question":"How are command-line options declared?"}'`. The response is HTTP 200. The answer mentions `@click.option`, and `sources` lists file paths from the repo.
- **Stream.** Run `curl -sN -H "Authorization: Bearer $(bash $V token)" -H 'Content-Type: application/json' -d '{"repo_id":"'$RID'","question":"What does Context do?"}' http://127.0.0.1:8765/chat/stream | tee .verify-runs/<run>/chat-stream.sse`. The output contains several `event: token` lines and ends with `event: done`.
- **History persisted.** Run `bash $V call chat-hist GET /chat/history/$RID`. The response contains both questions in order.
- **Clear.** Run `bash $V call chat-clear DELETE /chat/history/$RID`, which returns 204. A second history call returns an empty list.

## Gotchas

- Offline, chat returns 500 (database and OpenAI unreachable). Report it as unverified.
- Answers aren't deterministic. Assert on the structure (`sources` is non-empty and the paths exist in the repo), not on exact wording.
- `HYDE_ENABLED` and `RERANKER_ENABLED` change latency and cost. Record their values with the evidence when you compare runs.
