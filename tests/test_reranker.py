"""Tests for LLM-based reranking."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest

from code_explorer.services.chat_service import ChatService
from code_explorer.models.domain import RetrievedChunk


@pytest.mark.anyio
async def test_reranker_reorders_chunks():
    """Reranker reorders chunks based on LLM relevance scores."""
    service = ChatService.__new__(ChatService)
    service.settings = MagicMock()
    service.settings.reranker_model = "gpt-4o-mini"
    service.settings.reranker_enabled = True

    chunks = [
        RetrievedChunk(
            chunk_id=uuid4(), score=0.9, file_path="a.py",
            start_line=1, end_line=10, language="python", content="irrelevant code",
        ),
        RetrievedChunk(
            chunk_id=uuid4(), score=0.8, file_path="b.py",
            start_line=1, end_line=10, language="python", content="def verify_jwt(): ...",
        ),
    ]

    # LLM says chunk 2 (index 1) is more relevant
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '[{"index": 0, "score": 2}, {"index": 1, "score": 5}]'

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    service._client = mock_client

    result = await service._rerank_chunks(chunks, "how does auth work?")

    # Chunk at original index 1 should now be first
    assert result[0].file_path == "b.py"


@pytest.mark.anyio
async def test_reranker_fallback_on_error():
    """If reranker fails, return chunks in original order."""
    service = ChatService.__new__(ChatService)
    service.settings = MagicMock()
    service.settings.reranker_model = "gpt-4o-mini"
    service.settings.reranker_enabled = True

    chunks = [
        RetrievedChunk(
            chunk_id=uuid4(), score=0.9, file_path="a.py",
            start_line=1, end_line=10, language="python", content="code a",
        ),
    ]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
    service._client = mock_client

    result = await service._rerank_chunks(chunks, "question")

    assert result == chunks
