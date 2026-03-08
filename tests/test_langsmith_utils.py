"""Tests for LangSmith OpenAI client wrapping."""

from unittest.mock import MagicMock, patch

from openai import AsyncOpenAI

from code_explorer.utils.langsmith_utils import create_openai_client


def test_returns_plain_client_when_no_langsmith_key():
    """Without LangSmith API key, returns unwrapped AsyncOpenAI."""
    settings = MagicMock()
    settings.langsmith_api_key = None

    client = create_openai_client("sk-test", settings)
    assert isinstance(client, AsyncOpenAI)


def test_returns_wrapped_client_when_langsmith_key_set():
    """With LangSmith API key, returns wrapped AsyncOpenAI."""
    settings = MagicMock()
    settings.langsmith_api_key = MagicMock()
    settings.langsmith_api_key.get_secret_value.return_value = "lsv2_test"

    with patch("langsmith.wrappers.wrap_openai") as mock_wrap:
        mock_wrap.return_value = MagicMock(spec=AsyncOpenAI)
        client = create_openai_client("sk-test", settings)
        mock_wrap.assert_called_once()
