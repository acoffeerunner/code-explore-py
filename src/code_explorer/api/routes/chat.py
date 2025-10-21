"""Chat API routes for RAG-based Q&A."""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from code_explorer.api.middleware.auth import CurrentUser
from code_explorer.db.session import DbSessionDep
from code_explorer.models.db import ChatMessage, Repo
from code_explorer.models.requests import ChatRequest
from code_explorer.models.responses import (
    ChatHistoryResponse,
    ChatMessageResponse,
    ChatResponse,
)
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
    Messages are automatically saved to chat history.
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
        # Save user message
        user_message = ChatMessage(
            repo_id=repo_id,
            user_id=user.user_id,
            role="user",
            content=request.question,
        )
        db.add(user_message)
        await db.flush()

        # Get AI response
        response = await chat_service.query(
            db=db,
            repo_id=repo_id,
            user_id=user.user_id,
            question=request.question,
            model=request.model,
            top_k=request.top_k,
        )

        # Save assistant message with sources
        assistant_message = ChatMessage(
            repo_id=repo_id,
            user_id=user.user_id,
            role="assistant",
            content=response.answer,
            sources=[s.model_dump(mode="json") for s in response.sources] if response.sources else None,
        )
        db.add(assistant_message)
        await db.commit()

        await log.ainfo(
            "Chat request completed",
            source_count=len(response.sources),
            tokens_used=response.usage.total_tokens,
        )

        return response

    except ChatError as e:
        await db.rollback()
        await log.awarning("Chat query failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    except Exception as e:
        await db.rollback()
        await log.aerror("Chat request failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred processing your request",
        )


@router.get("/history/{repo_id}", response_model=ChatHistoryResponse)
async def get_chat_history(
    repo_id: UUID,
    user: CurrentUser,
    db: DbSessionDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ChatHistoryResponse:
    """
    Get chat history for a repository.

    Returns messages in chronological order (oldest first).
    Only the owner of a repository can access its chat history.
    """
    # Verify user owns the repo
    repo_result = await db.execute(
        select(Repo).where(Repo.id == repo_id, Repo.user_id == user.user_id)
    )
    repo = repo_result.scalar_one_or_none()

    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found or access denied",
        )

    # Get messages
    messages_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.repo_id == repo_id, ChatMessage.user_id == user.user_id)
        .order_by(ChatMessage.created_at.asc())
        .offset(offset)
        .limit(limit)
    )
    messages = messages_result.scalars().all()

    # Get total count
    from sqlalchemy import func
    count_result = await db.execute(
        select(func.count(ChatMessage.id))
        .where(ChatMessage.repo_id == repo_id, ChatMessage.user_id == user.user_id)
    )
    total = count_result.scalar() or 0

    return ChatHistoryResponse(
        messages=[
            ChatMessageResponse(
                id=m.id,
                role=m.role,
                content=m.content,
                sources=m.sources,
                created_at=m.created_at,
            )
            for m in messages
        ],
        total=total,
    )


@router.delete("/history/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_chat_history(
    repo_id: UUID,
    user: CurrentUser,
    db: DbSessionDep,
) -> None:
    """
    Clear all chat history for a repository.

    Only the owner of a repository can clear its chat history.
    """
    # Verify user owns the repo
    repo_result = await db.execute(
        select(Repo).where(Repo.id == repo_id, Repo.user_id == user.user_id)
    )
    repo = repo_result.scalar_one_or_none()

    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found or access denied",
        )

    # Delete all messages for this repo/user
    from sqlalchemy import delete
    await db.execute(
        delete(ChatMessage)
        .where(ChatMessage.repo_id == repo_id, ChatMessage.user_id == user.user_id)
    )
    await db.commit()
