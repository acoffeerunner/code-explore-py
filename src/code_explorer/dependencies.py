"""FastAPI dependency injection providers."""

from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends

from code_explorer.config import Settings, get_settings

# Type alias for settings dependency
SettingsDep = Annotated[Settings, Depends(get_settings)]


@lru_cache
def get_settings_cached() -> Settings:
    """Get cached settings instance."""
    return get_settings()


# Placeholder dependencies - will be implemented in later phases
# These are defined here to establish the pattern


async def get_db_session() -> Any:  # AsyncGenerator[AsyncSession, None]
    """
    Get database session.

    Will be implemented in Phase 2.
    """
    raise NotImplementedError("Database session not yet implemented")


async def get_redis_client() -> Any:
    """
    Get Redis client.

    Will be implemented in Phase 2.
    """
    raise NotImplementedError("Redis client not yet implemented")


async def get_pinecone_index() -> Any:
    """
    Get Pinecone index.

    Will be implemented in Phase 3.
    """
    raise NotImplementedError("Pinecone index not yet implemented")


async def get_openai_client() -> Any:
    """
    Get OpenAI client.

    Will be implemented in Phase 3.
    """
    raise NotImplementedError("OpenAI client not yet implemented")
