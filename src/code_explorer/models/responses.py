"""Pydantic response schemas for API endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from code_explorer.models.domain import RepoStatus, SourceCitation, TokenUsage


class BranchesResponse(BaseModel):
    """Response containing available branches for a Git URL."""

    url: str
    default_branch: str
    branches: list[str]
    is_public: bool
    error: str | None = None


class RepoResponse(BaseModel):
    """Response containing repository details."""

    id: UUID
    url: str
    branch: str
    status: RepoStatus
    progress_pct: int = 0
    branches_available: list[str] | None = None
    error_message: str | None = None
    file_count: int | None = None
    chunk_count: int | None = None
    webhook_url: str | None = None
    webhook_secret: str | None = Field(
        default=None,
        description="Only returned on creation - store securely!",
    )
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None = None


class RepoListResponse(BaseModel):
    """Paginated list of repositories."""

    items: list[RepoResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class RepoStatusResponse(BaseModel):
    """Lightweight response for status polling."""

    id: UUID
    status: RepoStatus
    progress_pct: int = 0
    error_message: str | None = None
    file_count: int | None = None
    chunk_count: int | None = None
    indexed_at: datetime | None = None


class ChatResponse(BaseModel):
    """Response from RAG-based chat query."""

    answer: str
    sources: list[SourceCitation]
    model: str
    usage: TokenUsage


class FileInfo(BaseModel):
    """Information about an indexed file."""

    path: str
    language: str
    chunk_count: int
    symbol_count: int


class FileListResponse(BaseModel):
    """List of indexed files in a repository."""

    files: list[FileInfo]
    total: int


class ChunkInfo(BaseModel):
    """Information about a code chunk."""

    id: UUID
    start_line: int
    end_line: int
    symbol_name: str | None
    symbol_type: str | None
    token_count: int


class FileChunksResponse(BaseModel):
    """Chunks for a specific file."""

    path: str
    language: str
    chunks: list[ChunkInfo]


class HealthResponse(BaseModel):
    """Health check response."""

    status: str


class ReadinessResponse(BaseModel):
    """Readiness check response with service status."""

    status: str
    checks: dict[str, str]


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: str | None = None
    request_id: str | None = None
