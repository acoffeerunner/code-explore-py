"""Tests for LangSmith configuration settings."""

from code_explorer.config import Settings


def test_langsmith_defaults():
    """LangSmith settings default to disabled (no API key)."""
    settings = Settings(
        supabase_url="https://test.supabase.co",
        supabase_anon_key="test-anon-key",
        supabase_service_role_key="test-service-key",
        supabase_jwt_secret="test-jwt-secret",
        database_url="postgresql+asyncpg://localhost/test",
        pinecone_api_key="test-pinecone-key",
        openai_api_key="test-openai-key",
    )
    assert settings.langsmith_api_key is None
    assert settings.langsmith_project == "code-explorer"


def test_langsmith_from_env(monkeypatch):
    """LangSmith settings can be set via environment variables."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
    monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")
    settings = Settings(
        supabase_url="https://test.supabase.co",
        supabase_anon_key="test-anon-key",
        supabase_service_role_key="test-service-key",
        supabase_jwt_secret="test-jwt-secret",
        database_url="postgresql+asyncpg://localhost/test",
        pinecone_api_key="test-pinecone-key",
        openai_api_key="test-openai-key",
    )
    assert settings.langsmith_api_key.get_secret_value() == "lsv2_test_key"
    assert settings.langsmith_project == "my-project"
