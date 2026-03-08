"""FastAPI application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from arq.connections import ArqRedis, create_pool
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from code_explorer.api.middleware.logging import RequestLoggingMiddleware
from code_explorer.api.middleware.rate_limit import RateLimitMiddleware
from code_explorer.api.routes import chat, git, health, repos
from code_explorer.config import get_settings
from code_explorer.db.session import close_db, get_engine
from code_explorer.utils.telemetry import (
    get_metrics_app,
    instrument_fastapi,
    setup_logging,
    setup_opentelemetry,
)
from code_explorer.workers.worker import WorkerSettings

# Configure structured logging on import
setup_logging()

logger = structlog.get_logger(__name__)

# Global ARQ Redis connection
_arq_redis: ArqRedis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler for startup/shutdown events."""
    global _arq_redis  # noqa: PLW0603

    settings = get_settings()
    log = logger.bind(log_level=settings.log_level)

    # Startup
    await log.ainfo("Starting Code Explorer API", version="0.1.0")

    # Initialize LangSmith tracing (if configured)
    if settings.langsmith_api_key:
        import os

        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key.get_secret_value()
        os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
        await log.ainfo("LangSmith tracing enabled", project=settings.langsmith_project)

    # Initialize database connection pool
    _ = get_engine(settings)
    await log.ainfo("Database connection pool initialized")

    # Initialize ARQ Redis connection for job enqueueing
    try:
        _arq_redis = await create_pool(WorkerSettings.redis_settings)
        repos.set_arq_redis(_arq_redis)
        await log.ainfo("ARQ Redis connection initialized")
    except Exception as e:
        await log.awarning("Failed to initialize ARQ Redis", error=str(e))

    # Setup OpenTelemetry
    setup_opentelemetry(settings)
    await log.ainfo("Observability initialized")

    yield

    # Shutdown
    await log.ainfo("Shutting down Code Explorer API")

    # Close ARQ Redis
    if _arq_redis:
        await _arq_redis.close()
        await log.ainfo("ARQ Redis connection closed")

    # Close database connections
    await close_db()
    await log.ainfo("Database connections closed")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Code Explorer API",
        description="Git repo code indexing and RAG-based Q&A service",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Add middleware (order matters - last added runs first)
    # 1. CORS middleware (runs first)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Request logging middleware
    app.add_middleware(RequestLoggingMiddleware, settings=settings)

    # 3. Rate limiting middleware
    app.add_middleware(RateLimitMiddleware, settings=settings)

    # Include API routers
    app.include_router(health.router, prefix="/health", tags=["Health"])
    app.include_router(git.router, prefix="/git", tags=["Git"])
    app.include_router(repos.router, prefix="/repos", tags=["Repos"])
    app.include_router(chat.router, prefix="/chat", tags=["Chat"])

    # Mount Prometheus metrics endpoint
    app.mount("/metrics", get_metrics_app())

    # Instrument with OpenTelemetry
    instrument_fastapi(app)

    return app


# Application instance for uvicorn
app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "code_explorer.main:app",
        host="0.0.0.0",  # noqa: S104
        port=8000,
        reload=True,
        log_level=settings.log_level.lower(),
    )
