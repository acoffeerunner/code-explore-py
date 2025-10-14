"""Repository CRUD service."""

from uuid import UUID

import structlog
from arq import ArqRedis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from code_explorer.models.db import Chunk, IndexVersion, Repo
from code_explorer.models.domain import RepoStatus
from code_explorer.models.responses import RepoListResponse, RepoResponse, RepoStatusResponse
from code_explorer.services.vector_service import VectorService
from code_explorer.services.webhook_service import WebhookService

logger = structlog.get_logger(__name__)


class RepoError(Exception):
    """Error in repository operations."""

    pass


class RepoNotFoundError(RepoError):
    """Repository not found."""

    pass


class RepoDuplicateError(RepoError):
    """Repository already exists."""

    pass


class RepoService:
    """Service for repository CRUD operations."""

    def __init__(
        self,
        webhook_service: WebhookService | None = None,
        vector_service: VectorService | None = None,
    ) -> None:
        """Initialize repo service."""
        self.webhook = webhook_service or WebhookService()
        self.vector = vector_service or VectorService()

    async def create(
        self,
        db: AsyncSession,
        user_id: UUID,
        url: str,
        branch: str,
        webhook_url: str | None = None,
        arq_redis: ArqRedis | None = None,
    ) -> RepoResponse:
        """
        Create a new repository and enqueue indexing job.

        Args:
            db: Database session
            user_id: Owner's user ID
            url: Git repository URL
            branch: Branch to index
            webhook_url: Optional webhook callback URL
            arq_redis: ARQ Redis connection for enqueueing jobs

        Returns:
            RepoResponse with webhook_secret (only on creation)
        """
        log = logger.bind(user_id=str(user_id), url=url, branch=branch)

        # Check for duplicate
        existing = await db.execute(
            select(Repo).where(
                Repo.user_id == user_id,
                Repo.url == url,
                Repo.branch == branch,
            )
        )
        if existing.scalar_one_or_none():
            raise RepoDuplicateError(f"Repository {url}:{branch} already exists")

        # Generate webhook secret if webhook URL provided
        webhook_secret = None
        if webhook_url:
            webhook_secret = self.webhook.generate_secret()

        # Create repo
        repo = Repo(
            user_id=user_id,
            url=url,
            branch=branch,
            status=RepoStatus.PENDING.value,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
        )
        db.add(repo)
        await db.flush()

        await log.ainfo("Repository created", repo_id=str(repo.id))

        # Enqueue indexing job
        if arq_redis:
            await arq_redis.enqueue_job("index_repo_task", str(repo.id))
            await log.ainfo("Indexing job enqueued")

        return RepoResponse(
            id=repo.id,
            url=repo.url,
            branch=repo.branch,
            status=RepoStatus(repo.status),
            progress_pct=repo.progress_pct or 0,
            branches_available=repo.branches_available,
            webhook_url=repo.webhook_url,
            webhook_secret=webhook_secret,  # Only returned on creation!
            created_at=repo.created_at,
            updated_at=repo.updated_at,
        )

    async def get(
        self,
        db: AsyncSession,
        repo_id: UUID,
        user_id: UUID,
    ) -> RepoResponse:
        """
        Get repository by ID.

        Args:
            db: Database session
            repo_id: Repository UUID
            user_id: Owner's user ID (for authorization)

        Returns:
            RepoResponse

        Raises:
            RepoNotFoundError: If repo not found or not owned by user
        """
        result = await db.execute(
            select(Repo).where(Repo.id == repo_id, Repo.user_id == user_id)
        )
        repo = result.scalar_one_or_none()

        if not repo:
            raise RepoNotFoundError(f"Repository {repo_id} not found")

        return RepoResponse(
            id=repo.id,
            url=repo.url,
            branch=repo.branch,
            status=RepoStatus(repo.status),
            progress_pct=repo.progress_pct or 0,
            branches_available=repo.branches_available,
            error_message=repo.error_message,
            file_count=repo.file_count,
            chunk_count=repo.chunk_count,
            webhook_url=repo.webhook_url,
            # Never return webhook_secret after creation!
            created_at=repo.created_at,
            updated_at=repo.updated_at,
            indexed_at=repo.indexed_at,
        )

    async def get_status(
        self,
        db: AsyncSession,
        repo_id: UUID,
        user_id: UUID,
    ) -> RepoStatusResponse:
        """Get lightweight status for polling."""
        result = await db.execute(
            select(Repo).where(Repo.id == repo_id, Repo.user_id == user_id)
        )
        repo = result.scalar_one_or_none()

        if not repo:
            raise RepoNotFoundError(f"Repository {repo_id} not found")

        return RepoStatusResponse(
            id=repo.id,
            status=RepoStatus(repo.status),
            progress_pct=repo.progress_pct or 0,
            error_message=repo.error_message,
            file_count=repo.file_count,
            chunk_count=repo.chunk_count,
            indexed_at=repo.indexed_at,
        )

    async def list(
        self,
        db: AsyncSession,
        user_id: UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> RepoListResponse:
        """
        List repositories for a user with pagination.

        Args:
            db: Database session
            user_id: Owner's user ID
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            RepoListResponse with pagination info
        """
        # Get total count
        count_result = await db.execute(
            select(func.count()).select_from(Repo).where(Repo.user_id == user_id)
        )
        total = count_result.scalar() or 0

        # Get paginated results
        offset = (page - 1) * page_size
        result = await db.execute(
            select(Repo)
            .where(Repo.user_id == user_id)
            .order_by(Repo.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        repos = result.scalars().all()

        items = [
            RepoResponse(
                id=r.id,
                url=r.url,
                branch=r.branch,
                status=RepoStatus(r.status),
                progress_pct=r.progress_pct or 0,
                branches_available=r.branches_available,
                error_message=r.error_message,
                file_count=r.file_count,
                chunk_count=r.chunk_count,
                webhook_url=r.webhook_url,
                created_at=r.created_at,
                updated_at=r.updated_at,
                indexed_at=r.indexed_at,
            )
            for r in repos
        ]

        return RepoListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            has_more=(offset + len(items)) < total,
        )

    async def delete(
        self,
        db: AsyncSession,
        repo_id: UUID,
        user_id: UUID,
    ) -> None:
        """
        Delete a repository and all associated data.

        Args:
            db: Database session
            repo_id: Repository UUID
            user_id: Owner's user ID
        """
        log = logger.bind(repo_id=str(repo_id))

        result = await db.execute(
            select(Repo).where(Repo.id == repo_id, Repo.user_id == user_id)
        )
        repo = result.scalar_one_or_none()

        if not repo:
            raise RepoNotFoundError(f"Repository {repo_id} not found")

        # Get all versions to cleanup Pinecone namespaces
        versions_result = await db.execute(
            select(IndexVersion).where(IndexVersion.repo_id == repo_id)
        )
        versions = versions_result.scalars().all()

        # Delete Pinecone namespaces
        for version in versions:
            try:
                await self.vector.delete_namespace(version.pinecone_namespace)
            except Exception as e:
                await log.awarning(
                    "Failed to delete Pinecone namespace",
                    namespace=version.pinecone_namespace,
                    error=str(e),
                )

        # Delete repo (cascades to versions, chunks, webhook_deliveries)
        await db.delete(repo)
        await db.flush()

        await log.ainfo("Repository deleted")

    async def reindex(
        self,
        db: AsyncSession,
        repo_id: UUID,
        user_id: UUID,
        arq_redis: ArqRedis | None = None,
        force: bool = False,
    ) -> RepoStatusResponse:
        """
        Trigger re-indexing of a repository.

        Args:
            db: Database session
            repo_id: Repository UUID
            user_id: Owner's user ID
            arq_redis: ARQ Redis connection
            force: Force reindex even if already in progress

        Returns:
            RepoStatusResponse
        """
        log = logger.bind(repo_id=str(repo_id))

        result = await db.execute(
            select(Repo).where(Repo.id == repo_id, Repo.user_id == user_id)
        )
        repo = result.scalar_one_or_none()

        if not repo:
            raise RepoNotFoundError(f"Repository {repo_id} not found")

        # Check if already indexing
        if repo.status in (RepoStatus.CLONING.value, RepoStatus.INDEXING.value) and not force:
            raise RepoError("Repository is already being indexed")

        # Reset status
        repo.status = RepoStatus.PENDING.value
        repo.progress_pct = 0
        repo.error_message = None
        await db.flush()

        # Enqueue job
        if arq_redis:
            await arq_redis.enqueue_job("index_repo_task", str(repo_id))
            await log.ainfo("Reindex job enqueued")

        return RepoStatusResponse(
            id=repo.id,
            status=RepoStatus(repo.status),
            progress_pct=repo.progress_pct or 0,
            error_message=repo.error_message,
            file_count=repo.file_count,
            chunk_count=repo.chunk_count,
            indexed_at=repo.indexed_at,
        )
