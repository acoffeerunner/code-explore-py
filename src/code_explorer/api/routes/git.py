"""Git-related API routes."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from code_explorer.api.middleware.auth import CurrentUser
from code_explorer.models.requests import BranchesRequest
from code_explorer.models.responses import BranchesResponse
from code_explorer.services.git_service import GitService, RepoNotFoundError

logger = structlog.get_logger(__name__)

router = APIRouter()


def get_git_service() -> GitService:
    """Dependency for Git service."""
    return GitService()


@router.post("/branches", response_model=BranchesResponse)
async def get_branches(
    request: BranchesRequest,
    user: CurrentUser,
    git_service: GitService = Depends(get_git_service),
) -> BranchesResponse:
    """
    Fetch available branches for a Git repository.

    This endpoint allows the frontend to populate a branch selector
    before submitting a repository for indexing.

    Requires authentication.
    """
    log = logger.bind(user_id=str(user.user_id), url=str(request.url))
    await log.ainfo("Fetching branches")

    try:
        default_branch, branches = await git_service.get_branches(str(request.url))

        return BranchesResponse(
            url=str(request.url),
            default_branch=default_branch,
            branches=branches,
            is_public=True,
        )

    except RepoNotFoundError as e:
        await log.awarning("Repository not accessible", error=str(e))
        return BranchesResponse(
            url=str(request.url),
            default_branch="",
            branches=[],
            is_public=False,
            error=str(e),
        )

    except Exception as e:
        await log.aerror("Failed to fetch branches", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch branches: {e}",
        )
