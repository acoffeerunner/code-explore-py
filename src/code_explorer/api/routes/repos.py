"""Repository API routes."""

from uuid import UUID

import structlog
from arq import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, status

from code_explorer.api.middleware.auth import CurrentUser
from code_explorer.db.session import DbSessionDep
from code_explorer.models.requests import CreateRepoRequest, ReindexRequest
from code_explorer.models.responses import (
    RepoListResponse,
    RepoResponse,
    RepoStatusResponse,
)
from code_explorer.services.repo_service import (
    RepoDuplicateError,
    RepoError,
    RepoNotFoundError,
    RepoService,
)

logger = structlog.get_logger(__name__)

router = APIRouter()

# Global ARQ Redis connection (set during app startup)
_arq_redis: ArqRedis | None = None


def set_arq_redis(redis: ArqRedis) -> None:
    """Set the ARQ Redis connection."""
    global _arq_redis  # noqa: PLW0603
    _arq_redis = redis


def get_arq_redis() -> ArqRedis | None:
    """Get the ARQ Redis connection."""
    return _arq_redis


def get_repo_service() -> RepoService:
    """Dependency for repo service."""
    return RepoService()


@router.post("", response_model=RepoResponse, status_code=status.HTTP_201_CREATED)
async def create_repo(
    request: CreateRepoRequest,
    user: CurrentUser,
    db: DbSessionDep,
    repo_service: RepoService = Depends(get_repo_service),
) -> RepoResponse:
    """
    Create a new repository indexing job.

    The repository will be cloned and indexed asynchronously.
    Poll GET /repos/{id}/status to check progress.

    If a webhook_url is provided, you will receive the webhook_secret
    in the response. **Store it securely** - it will not be returned again.
    """
    log = logger.bind(
        user_id=str(user.user_id),
        url=str(request.url),
        branch=request.branch,
    )
    await log.ainfo("Creating repository")

    try:
        # Use default branch if not specified
        branch = request.branch
        if not branch:
            from code_explorer.services.git_service import GitService

            git_service = GitService()
            default_branch, _ = await git_service.get_branches(str(request.url))
            branch = default_branch

        repo = await repo_service.create(
            db=db,
            user_id=user.user_id,
            url=str(request.url),
            branch=branch,
            webhook_url=str(request.webhook_url) if request.webhook_url else None,
            arq_redis=get_arq_redis(),
        )
        await db.commit()
        return repo

    except RepoDuplicateError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repository with this URL and branch already exists",
        )

    except Exception as e:
        await log.aerror("Failed to create repository", error=str(e))
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.get("", response_model=RepoListResponse)
async def list_repos(
    user: CurrentUser,
    db: DbSessionDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    repo_service: RepoService = Depends(get_repo_service),
) -> RepoListResponse:
    """
    List all repositories owned by the authenticated user.

    Supports pagination with page and page_size query parameters.
    """
    return await repo_service.list(
        db=db,
        user_id=user.user_id,
        page=page,
        page_size=page_size,
    )


@router.get("/{repo_id}", response_model=RepoResponse)
async def get_repo(
    repo_id: UUID,
    user: CurrentUser,
    db: DbSessionDep,
    repo_service: RepoService = Depends(get_repo_service),
) -> RepoResponse:
    """
    Get repository details by ID.

    Only the owner can access their repositories.
    """
    try:
        return await repo_service.get(db=db, repo_id=repo_id, user_id=user.user_id)
    except RepoNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )


@router.get("/{repo_id}/status", response_model=RepoStatusResponse)
async def get_repo_status(
    repo_id: UUID,
    user: CurrentUser,
    db: DbSessionDep,
    repo_service: RepoService = Depends(get_repo_service),
) -> RepoStatusResponse:
    """
    Get lightweight repository status for polling.

    Use this endpoint for status polling during indexing.
    """
    try:
        return await repo_service.get_status(db=db, repo_id=repo_id, user_id=user.user_id)
    except RepoNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repo(
    repo_id: UUID,
    user: CurrentUser,
    db: DbSessionDep,
    repo_service: RepoService = Depends(get_repo_service),
) -> None:
    """
    Delete a repository and all associated data.

    This will delete:
    - The repository record
    - All index versions
    - All stored chunks
    - All webhook delivery records
    - All vectors in Pinecone

    This action is irreversible.
    """
    log = logger.bind(user_id=str(user.user_id), repo_id=str(repo_id))
    await log.ainfo("Deleting repository")

    try:
        await repo_service.delete(db=db, repo_id=repo_id, user_id=user.user_id)
        await db.commit()
    except RepoNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )


@router.post("/{repo_id}/reindex", response_model=RepoStatusResponse)
async def reindex_repo(
    repo_id: UUID,
    request: ReindexRequest,
    user: CurrentUser,
    db: DbSessionDep,
    repo_service: RepoService = Depends(get_repo_service),
) -> RepoStatusResponse:
    """
    Trigger re-indexing of a repository.

    Use force=true to reindex even if already in progress.
    """
    log = logger.bind(user_id=str(user.user_id), repo_id=str(repo_id))
    await log.ainfo("Triggering reindex")

    try:
        status_response = await repo_service.reindex(
            db=db,
            repo_id=repo_id,
            user_id=user.user_id,
            arq_redis=get_arq_redis(),
            force=request.force,
        )
        await db.commit()
        return status_response

    except RepoNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )
    except RepoError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
