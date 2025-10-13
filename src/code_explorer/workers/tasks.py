"""ARQ task definitions for background processing."""

from uuid import UUID

import structlog
from arq import Retry

from code_explorer.db.session import get_session_factory
from code_explorer.services.indexing_service import IndexingService

logger = structlog.get_logger(__name__)


async def index_repo_task(ctx: dict, repo_id: str) -> dict:
    """
    ARQ task to index a repository.

    Args:
        ctx: ARQ context (contains redis connection)
        repo_id: Repository UUID as string

    Returns:
        Dict with task result
    """
    log = logger.bind(repo_id=repo_id, job_id=ctx.get("job_id"))
    await log.ainfo("Starting index_repo_task")

    try:
        repo_uuid = UUID(repo_id)
    except ValueError:
        await log.aerror("Invalid repo_id format")
        return {"status": "error", "message": "Invalid repo_id format"}

    # Get database session
    session_factory = get_session_factory()

    async with session_factory() as db:
        try:
            indexing_service = IndexingService()
            await indexing_service.index_repository(db, repo_uuid)

            await log.ainfo("index_repo_task completed successfully")
            return {"status": "success", "repo_id": repo_id}

        except Exception as e:
            await log.aerror("index_repo_task failed", error=str(e))

            # Retry on transient errors (up to 3 times with exponential backoff)
            if ctx.get("job_try", 1) < 3:
                raise Retry(defer=ctx.get("job_try", 1) * 60)  # 1min, 2min, 3min

            return {"status": "failed", "repo_id": repo_id, "error": str(e)}


async def cleanup_stale_versions_task(ctx: dict) -> dict:
    """
    ARQ task to garbage collect stale index versions.

    This task:
    1. Finds versions marked as 'stale' older than TTL
    2. Deletes their Pinecone namespaces
    3. Marks them as 'deleted' (Postgres cascade handles chunks)
    """
    log = logger.bind(job_id=ctx.get("job_id"))
    await log.ainfo("Starting cleanup_stale_versions_task")

    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select, update

    from code_explorer.config import get_settings
    from code_explorer.models.db import IndexVersion
    from code_explorer.models.domain import VersionStatus
    from code_explorer.services.vector_service import VectorService

    settings = get_settings()
    session_factory = get_session_factory()
    vector_service = VectorService()

    ttl = timedelta(hours=settings.stale_version_ttl_hours)
    cutoff = datetime.now(UTC) - ttl

    async with session_factory() as db:
        # Find stale versions older than TTL
        result = await db.execute(
            select(IndexVersion).where(
                IndexVersion.status == VersionStatus.STALE.value,
                IndexVersion.completed_at < cutoff,
            )
        )
        stale_versions = result.scalars().all()

        await log.ainfo("Found stale versions", count=len(stale_versions))

        deleted_count = 0
        for version in stale_versions:
            try:
                # Delete Pinecone namespace
                await vector_service.delete_namespace(version.pinecone_namespace)

                # Mark as deleted (chunks cascade delete)
                await db.execute(
                    update(IndexVersion)
                    .where(IndexVersion.id == version.id)
                    .values(status=VersionStatus.DELETED.value)
                )
                deleted_count += 1

            except Exception as e:
                await log.awarning(
                    "Failed to cleanup version",
                    version_id=str(version.id),
                    error=str(e),
                )

        await db.commit()

    await log.ainfo("Cleanup complete", deleted_count=deleted_count)
    return {"status": "success", "deleted_count": deleted_count}


async def retry_webhooks_task(ctx: dict) -> dict:
    """
    ARQ task to retry failed webhook deliveries.

    Runs periodically to pick up pending deliveries and retry them.
    """
    log = logger.bind(job_id=ctx.get("job_id"))
    await log.ainfo("Starting retry_webhooks_task")

    from datetime import UTC, datetime

    from sqlalchemy import select

    from code_explorer.models.db import WebhookDelivery
    from code_explorer.models.domain import DeliveryStatus
    from code_explorer.services.webhook_service import WebhookService

    session_factory = get_session_factory()
    webhook_service = WebhookService()

    async with session_factory() as db:
        # Find pending deliveries ready for retry
        result = await db.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.status == DeliveryStatus.PENDING.value,
                WebhookDelivery.next_retry_at <= datetime.now(UTC),
            )
        )
        pending = result.scalars().all()

        await log.ainfo("Found pending webhook deliveries", count=len(pending))

        for delivery in pending:
            try:
                await webhook_service.deliver(db, delivery)
            except Exception as e:
                await log.awarning(
                    "Webhook delivery failed",
                    delivery_id=str(delivery.id),
                    error=str(e),
                )

        await db.commit()

    await log.ainfo("Webhook retry complete")
    return {"status": "success"}
