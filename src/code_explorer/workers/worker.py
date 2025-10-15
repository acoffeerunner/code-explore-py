"""ARQ worker entry point and configuration."""

import asyncio
from typing import Any

import structlog
from arq import cron
from arq.connections import RedisSettings

from code_explorer.config import get_settings
from code_explorer.workers.tasks import (
    cleanup_stale_versions_task,
    index_repo_task,
    retry_webhooks_task,
)

logger = structlog.get_logger(__name__)


async def startup(ctx: dict) -> None:
    """Worker startup hook."""
    settings = get_settings()
    logger.info(
        "ARQ worker starting",
        log_level=settings.log_level,
    )


async def shutdown(ctx: dict) -> None:
    """Worker shutdown hook."""
    logger.info("ARQ worker shutting down")


class WorkerSettings:
    """ARQ worker configuration."""

    # Task functions
    functions = [
        index_repo_task,
        cleanup_stale_versions_task,
        retry_webhooks_task,
    ]

    # Cron jobs (scheduled tasks)
    cron_jobs = [
        # Cleanup stale versions every hour
        cron(cleanup_stale_versions_task, hour={0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}, minute=0),
        # Retry webhooks every 5 minutes
        cron(retry_webhooks_task, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
    ]

    # Lifecycle hooks
    on_startup = startup
    on_shutdown = shutdown

    # Worker settings
    max_jobs = 10
    job_timeout = 600  # 10 minutes max per job
    keep_result = 3600  # Keep results for 1 hour
    retry_jobs = True


def _get_redis_settings() -> RedisSettings:
    """Get Redis settings from config."""
    settings = get_settings()
    # Parse redis URL
    # Format: redis://[:password@]host[:port][/database]
    url = settings.redis_url
    if url.startswith("redis://"):
        url = url[8:]

    # Extract password if present
    password = None
    if "@" in url:
        auth_part, url = url.split("@", 1)
        if ":" in auth_part:
            password = auth_part.split(":", 1)[1]

    # Extract host and port
    if "/" in url:
        host_port, db = url.split("/", 1)
        database = int(db) if db else 0
    else:
        host_port = url
        database = 0

    if ":" in host_port:
        host, port_str = host_port.split(":", 1)
        port = int(port_str)
    else:
        host = host_port
        port = 6379

    return RedisSettings(
        host=host,
        port=port,
        password=password,
        database=database,
    )


# Set redis_settings as class attribute after class definition
WorkerSettings.redis_settings = _get_redis_settings()


def run_worker() -> None:
    """Run the ARQ worker."""
    from arq import run_worker as arq_run_worker

    arq_run_worker(WorkerSettings)  # type: ignore[arg-type]


if __name__ == "__main__":
    run_worker()
