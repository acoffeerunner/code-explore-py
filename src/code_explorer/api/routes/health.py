"""Health check API routes."""

from typing import Any

import structlog
from fastapi import APIRouter
from sqlalchemy import text

from code_explorer.models.responses import HealthResponse, ReadinessResponse

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get("", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Liveness probe endpoint.

    Returns 200 OK if the service is running.
    Used by Kubernetes/container orchestrators for liveness checks.
    """
    return HealthResponse(status="healthy")


@router.get("/ready", response_model=ReadinessResponse)
async def readiness_check() -> ReadinessResponse:
    """
    Readiness probe endpoint.

    Checks connectivity to required services:
    - Database (PostgreSQL/Supabase)
    - Redis (for task queue)
    - Pinecone (vector database)

    Returns 200 OK with "ready" status if all services are accessible.
    Returns 200 OK with "degraded" status if some services are unavailable.
    """
    checks: dict[str, str] = {}

    # Check database
    try:
        from code_explorer.db.session import get_session_factory

        session_factory = get_session_factory()
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        logger.warning("Database health check failed", error=str(e))
        checks["database"] = "error"

    # Check Redis
    try:
        import redis.asyncio as redis

        from code_explorer.config import get_settings

        settings = get_settings()
        r = redis.from_url(settings.redis_url)
        await r.ping()
        await r.close()
        checks["redis"] = "ok"
    except Exception as e:
        logger.warning("Redis health check failed", error=str(e))
        checks["redis"] = "error"

    # Check Pinecone (just verify client can be created)
    try:
        from code_explorer.services.vector_service import VectorService

        vector_service = VectorService()
        # Just access the client property to verify connection
        _ = vector_service.client
        checks["pinecone"] = "ok"
    except Exception as e:
        logger.warning("Pinecone health check failed", error=str(e))
        checks["pinecone"] = "error"

    all_healthy = all(v == "ok" for v in checks.values())
    status = "ready" if all_healthy else "degraded"

    return ReadinessResponse(status=status, checks=checks)
