"""Tests for chat history context rewriting."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from code_explorer.services.query_service import QueryService


@pytest.mark.anyio
async def test_rewrite_follow_up_question():
    """Follow-up question is rewritten as standalone using chat history."""
    service = QueryService()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "How does the JWT authentication middleware handle errors?"

    history = [
        {"role": "user", "content": "how does auth work?"},
        {"role": "assistant", "content": "The auth uses JWT middleware..."},
    ]

    with patch.object(service, '_get_openai_client') as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await service.rewrite_with_history("what about the error handling there?", history)

    assert "JWT" in result or "authentication" in result.lower()


@pytest.mark.anyio
async def test_rewrite_returns_original_when_no_history():
    """Return original question when history is empty."""
    service = QueryService()
    result = await service.rewrite_with_history("how does auth work?", [])
    assert result == "how does auth work?"


@pytest.mark.anyio
async def test_rewrite_falls_back_on_error():
    """Return original question if LLM call fails."""
    service = QueryService()
    history = [
        {"role": "user", "content": "how does auth work?"},
        {"role": "assistant", "content": "It uses JWT."},
    ]

    with patch.object(service, '_get_openai_client') as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(side_effect=Exception("fail"))
        result = await service.rewrite_with_history("what about errors?", history)

    assert result == "what about errors?"
