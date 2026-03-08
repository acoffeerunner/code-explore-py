"""LangSmith integration utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openai import AsyncOpenAI

if TYPE_CHECKING:
    from code_explorer.config import Settings


def create_openai_client(api_key: str, settings: Settings) -> AsyncOpenAI:
    """Create an AsyncOpenAI client, optionally wrapped with LangSmith tracing.

    If settings.langsmith_api_key is set, the client is wrapped with
    LangSmith's wrap_openai for automatic trace collection on all
    chat.completions.create and embeddings.create calls.
    """
    client = AsyncOpenAI(api_key=api_key)

    if settings.langsmith_api_key:
        from langsmith.wrappers import wrap_openai

        return wrap_openai(client)

    return client
