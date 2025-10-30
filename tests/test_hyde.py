"""Tests for HyDE (Hypothetical Document Embedding) generation."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from code_explorer.services.query_service import QueryService


def _make_service_with_mock_settings() -> QueryService:
    """Create a QueryService with mock settings for testing."""
    service = QueryService()
    mock_settings = MagicMock()
    mock_settings.hyde_model = "gpt-4o-mini"
    service.settings = mock_settings
    return service


@pytest.mark.anyio
async def test_hyde_generates_hypothetical_code():
    """HyDE calls LLM and returns the hypothetical code snippet."""
    service = _make_service_with_mock_settings()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "def verify_jwt(token):\n    return jwt.decode(token)"

    with patch.object(service, '_get_openai_client') as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await service.generate_hyde("how does authentication work?")

    assert "verify_jwt" in result
    assert "jwt.decode" in result


@pytest.mark.anyio
async def test_hyde_falls_back_to_original_on_error():
    """If HyDE LLM call fails, return the original question."""
    service = _make_service_with_mock_settings()

    with patch.object(service, '_get_openai_client') as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
        result = await service.generate_hyde("how does auth work?")

    assert result == "how does auth work?"
