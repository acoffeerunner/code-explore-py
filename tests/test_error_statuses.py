"""Client-error statuses that must not surface as 500."""

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from code_explorer.api.middleware.auth import AuthenticatedUser, verify_supabase_token
from code_explorer.api.middleware.rate_limit import RateLimitMiddleware
from code_explorer.api.routes import git
from code_explorer.config import Settings
from code_explorer.services.git_service import GitService

SETTINGS = Settings(
    _env_file=None,
    supabase_url="http://127.0.0.1:9",
    supabase_anon_key="x",
    supabase_service_role_key="x",
    supabase_jwt_secret="x",
    database_url="postgresql+asyncpg://x:x@127.0.0.1:9/x",
    pinecone_api_key="x",
    openai_api_key="x",
)


def test_rate_limit_exceeded_returns_429_with_retry_after(monkeypatch) -> None:
    async def deny(self, key, limit, window_seconds):
        return False, 0, 4_102_444_800

    monkeypatch.setattr(RateLimitMiddleware, "check_rate_limit", deny)
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, settings=SETTINGS)
    app.get("/x")(lambda: {"ok": True})

    resp = TestClient(app, raise_server_exceptions=False).get("/x")

    assert resp.status_code == 429
    assert resp.json() == {"detail": "Rate limit exceeded. Please slow down."}
    assert int(resp.headers["retry-after"]) > 0
    assert resp.headers["x-ratelimit-remaining"] == "0"


def test_branches_for_disallowed_host_returns_400() -> None:
    app = FastAPI()
    app.include_router(git.router, prefix="/git")
    app.dependency_overrides[verify_supabase_token] = lambda: AuthenticatedUser(user_id=uuid4())
    app.dependency_overrides[git.get_git_service] = lambda: GitService(settings=SETTINGS)

    resp = TestClient(app).post("/git/branches", json={"url": "https://evil.example.com/a/b"})

    assert resp.status_code == 400
    assert "not in the allowed hosts list" in resp.json()["detail"]
