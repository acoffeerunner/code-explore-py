"""Tests for streaming SSE chat endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.anyio
async def test_stream_yields_sse_events():
    """Streaming query yields token events and a final done event."""
    from code_explorer.services.chat_service import ChatService

    service = ChatService.__new__(ChatService)
    service.settings = MagicMock()
    service.settings.default_chat_model = "gpt-4o-mini"

    # Mock a streaming response
    async def mock_stream():
        chunks = [
            MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello"))]),
            MagicMock(choices=[MagicMock(delta=MagicMock(content=" world"))]),
            MagicMock(choices=[MagicMock(delta=MagicMock(content=None))]),
        ]
        for chunk in chunks:
            yield chunk

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_stream())
    service._client = mock_client

    collected = []
    async for event in service._stream_llm_response(
        model="gpt-4o-mini",
        system_prompt="You are helpful.",
        user_prompt="Hello?",
    ):
        collected.append(event)

    # Should have token events for "Hello" and " world"
    token_events = [e for e in collected if e["event"] == "token"]
    assert len(token_events) == 2
    assert token_events[0]["data"]["content"] == "Hello"
    assert token_events[1]["data"]["content"] == " world"

    # Should have a content_done event
    done_events = [e for e in collected if e["event"] == "content_done"]
    assert len(done_events) == 1
    assert done_events[0]["data"]["full_content"] == "Hello world"
