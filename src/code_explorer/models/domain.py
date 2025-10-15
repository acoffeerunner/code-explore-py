"""Core domain models (Pydantic)."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RepoStatus(str, Enum):
    """Repository indexing status."""

    PENDING = "pending"
    CLONING = "cloning"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class VersionStatus(str, Enum):
    """Index version status."""

    BUILDING = "building"
    ACTIVE = "active"
    STALE = "stale"
    DELETED = "deleted"


class DeliveryStatus(str, Enum):
    """Webhook delivery status."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    EXHAUSTED = "exhausted"


class SymbolType(str, Enum):
    """Code symbol types extracted by tree-sitter."""

    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    INTERFACE = "interface"
    MODULE = "module"
    VARIABLE = "variable"


class Language(str, Enum):
    """Supported programming languages."""

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    GO = "go"
    JAVA = "java"
    CSHARP = "csharp"
    MARKDOWN = "markdown"
    UNKNOWN = "unknown"

    @classmethod
    def from_extension(cls, ext: str) -> "Language":
        """Get language from file extension."""
        mapping = {
            ".py": cls.PYTHON,
            ".js": cls.JAVASCRIPT,
            ".jsx": cls.JAVASCRIPT,
            ".mjs": cls.JAVASCRIPT,
            ".ts": cls.TYPESCRIPT,
            ".tsx": cls.TYPESCRIPT,
            ".go": cls.GO,
            ".java": cls.JAVA,
            ".cs": cls.CSHARP,
            ".md": cls.MARKDOWN,
        }
        return mapping.get(ext.lower(), cls.UNKNOWN)


class CodeChunk(BaseModel):
    """Domain model for a code chunk."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version_id: UUID
    repo_id: UUID
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str | None = None
    symbol_type: SymbolType | None = None
    language: Language
    content: str
    token_count: int
    content_hash: str


class PineconeMetadata(BaseModel):
    """Metadata stored in Pinecone (references only, no content)."""

    file_path: str
    start_line: int
    end_line: int
    symbol_name: str | None = None
    symbol_type: str | None = None
    language: str


class RetrievedChunk(BaseModel):
    """A chunk retrieved from vector search with its score."""

    chunk_id: UUID
    score: float
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str | None = None
    symbol_type: str | None = None
    language: str
    content: str  # Fetched from Postgres


class SourceCitation(BaseModel):
    """A validated source citation for a chat response."""

    chunk_id: UUID
    file: str
    line_start: int
    line_end: int
    symbol_name: str | None = None
    score: float = Field(description="Similarity score from vector search")


class TokenUsage(BaseModel):
    """Token usage statistics for a chat request."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class User(BaseModel):
    """Authenticated user from Supabase JWT."""

    user_id: UUID
    email: str | None = None
    role: str = "authenticated"


class WebhookEvent(str, Enum):
    """Webhook event types."""

    REPO_INDEXED = "repo.indexed"
    REPO_FAILED = "repo.failed"


class WebhookPayload(BaseModel):
    """Webhook payload sent to user's webhook URL."""

    event: WebhookEvent
    repo_id: UUID
    url: str
    branch: str
    commit_hash: str
    status: RepoStatus
    timestamp: datetime
    idempotency_key: str = Field(
        description="Format: {repo_id}:{status}:{commit_hash}"
    )
