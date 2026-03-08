"""Tests for new RAG pipeline configuration settings."""

import os
import pytest
from code_explorer.config import Settings


def test_default_rag_settings():
    """New RAG settings have correct defaults."""
    settings = Settings(
        _env_file=None,
        supabase_url="https://test.supabase.co",
        supabase_anon_key="test-anon-key",
        supabase_service_role_key="test-service-key",
        supabase_jwt_secret="test-jwt-secret",
        database_url="postgresql+asyncpg://localhost/test",
        pinecone_api_key="test-pinecone-key",
        openai_api_key="test-openai-key",
    )
    assert settings.reranker_model == "gpt-4o-mini"
    assert settings.hyde_model == "gpt-4o-mini"
    assert settings.hyde_enabled is True
    assert settings.reranker_enabled is True
    assert settings.min_similarity_score == 0.3
    assert settings.chat_history_turns == 5


def test_rag_settings_from_env(monkeypatch):
    """RAG settings can be overridden via environment variables."""
    monkeypatch.setenv("RERANKER_MODEL", "gpt-4o")
    monkeypatch.setenv("HYDE_ENABLED", "false")
    monkeypatch.setenv("MIN_SIMILARITY_SCORE", "0.5")
    monkeypatch.setenv("CHAT_HISTORY_TURNS", "3")
    settings = Settings(
        _env_file=None,
        supabase_url="https://test.supabase.co",
        supabase_anon_key="test-anon-key",
        supabase_service_role_key="test-service-key",
        supabase_jwt_secret="test-jwt-secret",
        database_url="postgresql+asyncpg://localhost/test",
        pinecone_api_key="test-pinecone-key",
        openai_api_key="test-openai-key",
    )
    assert settings.reranker_model == "gpt-4o"
    assert settings.hyde_enabled is False
    assert settings.min_similarity_score == 0.5
    assert settings.chat_history_turns == 3
