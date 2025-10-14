"""Chat API routes for RAG-based Q&A."""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from code_explorer.api.middleware.auth import CurrentUser
from code_explorer.db.session import DbSessionDep
from code_explorer.models.requests import ChatRequest
from code_explorer.models.responses import ChatResponse
from code_explorer.services.chat_service import ChatError, ChatService

logger = structlog.get_logger(__name__)

router = APIRouter()


def get_chat_service() -> ChatService:
    """Dependency for chat service."""
    return ChatService()


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: CurrentUser,
    db: DbSessionDep,
    chat_service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    """
    Ask a question about a repository's code.

    Uses RAG (Retrieval Augmented Generation) to:
    1. Find relevant code chunks from the indexed repository
    2. Generate an answer using an LLM with the retrieved context
    3. Return the answer with source citations

    The response includes:
    - `answer`: The generated response with inline citations [1], [2], etc.
    - `sources`: List of code locations that were cited
    - `model`: The LLM model used
    - `usage`: Token usage statistics

    Only the owner of a repository can query it.
    """
    log = logger.bind(
        user_id=str(user.user_id),
        repo_id=request.repo_id,
        question_length=len(request.question),
    )
    await log.ainfo("Processing chat request")

    try:
        repo_id = UUID(request.repo_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid repo_id format",
        )

    try:
        response = await chat_service.query(
            db=db,
            repo_id=repo_id,
            user_id=user.user_id,
            question=request.question,
            model=request.model,
            top_k=request.top_k,
        )

        await log.ainfo(
            "Chat request completed",
            source_count=len(response.sources),
            tokens_used=response.usage.total_tokens,
        )

        return response

    except ChatError as e:
        await log.awarning("Chat query failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    except Exception as e:
        await log.aerror("Chat request failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred processing your request",
        )
