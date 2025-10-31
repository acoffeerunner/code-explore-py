"""Pydantic request schemas for API endpoints."""

from pydantic import BaseModel, Field, HttpUrl, field_validator


class BranchesRequest(BaseModel):
    """Request to fetch available branches for a Git URL."""

    url: HttpUrl = Field(description="Public Git repository URL (HTTPS only)")

    @field_validator("url")
    @classmethod
    def validate_https(cls, v: HttpUrl) -> HttpUrl:
        """Ensure URL uses HTTPS."""
        if str(v).startswith("http://"):
            msg = "Only HTTPS URLs are allowed"
            raise ValueError(msg)
        return v


class CreateRepoRequest(BaseModel):
    """Request to create a new repository indexing job."""

    url: HttpUrl = Field(description="Public Git repository URL (HTTPS only)")
    branch: str | None = Field(
        default=None,
        description="Branch to index (defaults to repository's default branch)",
    )
    webhook_url: HttpUrl | None = Field(
        default=None,
        description="Optional webhook URL for completion notifications",
    )

    @field_validator("url")
    @classmethod
    def validate_https(cls, v: HttpUrl) -> HttpUrl:
        """Ensure URL uses HTTPS."""
        if str(v).startswith("http://"):
            msg = "Only HTTPS URLs are allowed"
            raise ValueError(msg)
        return v

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_https(cls, v: HttpUrl | None) -> HttpUrl | None:
        """Ensure webhook URL uses HTTPS."""
        if v is not None and str(v).startswith("http://"):
            msg = "Only HTTPS webhook URLs are allowed"
            raise ValueError(msg)
        return v


class ChatRequest(BaseModel):
    """Request for RAG-based chat query."""

    repo_id: str = Field(description="UUID of the indexed repository")
    question: str = Field(
        min_length=1,
        max_length=4000,
        description="Natural language question about the code",
    )
    model: str | None = Field(
        default=None,
        description="LLM model to use (defaults to gpt-4o-mini)",
    )
    top_k: int = Field(
        default=15,
        ge=1,
        le=50,
        description="Number of chunks to retrieve for context",
    )
    history: list[dict[str, str]] | None = Field(
        default=None,
        description="Previous chat turns for context [{'role': 'user'|'assistant', 'content': '...'}]",
    )


class ReindexRequest(BaseModel):
    """Request to trigger re-indexing of a repository."""

    force: bool = Field(
        default=False,
        description="Force reindex even if commit hash unchanged",
    )
