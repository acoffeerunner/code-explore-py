"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Use asyncio for async tests."""
    return "asyncio"


# TODO: Add fixtures for:
# - Test database session
# - Mock Redis client
# - Mock Pinecone client
# - Mock OpenAI client
# - Authenticated test client
