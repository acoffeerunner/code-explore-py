"""Async database session management."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from code_explorer.config import Settings, get_settings

# Global engine and session factory (initialized on first use)
_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Get or create the async database engine."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        if settings is None:
            settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.log_level == "DEBUG",
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=300,  # Recycle connections after 5 minutes
        )
    return _engine


def get_session_factory(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    """Get or create the async session factory."""
    global _async_session_factory  # noqa: PLW0603
    if _async_session_factory is None:
        engine = get_engine(settings)
        _async_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _async_session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides an async database session.

    Usage:
        @router.get("/example")
        async def example(db: DbSessionDep):
            result = await db.execute(select(Model))
            ...
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Type alias for dependency injection
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


async def close_db() -> None:
    """Close database connections. Call on application shutdown."""
    global _engine, _async_session_factory  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None
