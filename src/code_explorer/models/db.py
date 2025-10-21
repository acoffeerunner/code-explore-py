"""SQLAlchemy ORM models for the database layer."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

if TYPE_CHECKING:
    pass


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class Repo(Base):
    """Repository model - represents a Git repository indexed by a user."""

    __tablename__ = "repos"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="References auth.users(id) in Supabase",
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    branch: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    branches_available: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="HMAC secret for webhook signatures - NEVER LOG THIS",
    )
    file_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_version_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        comment="Current active index version",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    versions: Mapped[list["IndexVersion"]] = relationship(
        "IndexVersion",
        back_populates="repo",
        cascade="all, delete-orphan",
    )
    chunks: Mapped[list["Chunk"]] = relationship(
        "Chunk",
        back_populates="repo",
        cascade="all, delete-orphan",
    )
    webhook_deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        "WebhookDelivery",
        back_populates="repo",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="repo",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'cloning', 'indexing', 'ready', 'failed')",
            name="valid_status",
        ),
        UniqueConstraint("user_id", "url", "branch", name="unique_user_repo_branch"),
        Index("idx_repos_status", "status"),
        Index("idx_repos_url_branch", "url", "branch"),
    )


class IndexVersion(Base):
    """Index version model - represents a specific indexed version of a repo."""

    __tablename__ = "index_versions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    repo_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("repos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    commit_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    pinecone_namespace: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Format: {repo_id}:{commit_hash}",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="building",
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    repo: Mapped["Repo"] = relationship("Repo", back_populates="versions")
    chunks: Mapped[list["Chunk"]] = relationship(
        "Chunk",
        back_populates="version",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("repo_id", "commit_hash", name="unique_repo_commit"),
        CheckConstraint(
            "status IN ('building', 'active', 'stale', 'deleted')",
            name="valid_version_status",
        ),
        Index("idx_versions_status", "status"),
    )


class Chunk(Base):
    """Chunk model - represents a code chunk (source-of-truth for content)."""

    __tablename__ = "chunks"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("index_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    repo_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("repos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="function, class, method, interface",
    )
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Full chunk text - source of truth",
    )
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="SHA256 of content for deduplication",
    )
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relationships
    version: Mapped["IndexVersion"] = relationship("IndexVersion", back_populates="chunks")
    repo: Mapped["Repo"] = relationship("Repo", back_populates="chunks")

    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "file_path",
            "start_line",
            "end_line",
            name="unique_chunk_location",
        ),
    )


class ChatMessage(Base):
    """Chat message model - stores conversation history for each repo."""

    __tablename__ = "chat_messages"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    repo_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("repos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="References auth.users(id) in Supabase",
    )
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="user or assistant",
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Source citations for assistant messages",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relationships
    repo: Mapped["Repo"] = relationship("Repo", back_populates="messages")

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="valid_message_role",
        ),
        Index("idx_messages_repo_created", "repo_id", "created_at"),
    )


class WebhookDelivery(Base):
    """Webhook delivery model - tracks webhook delivery attempts."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    repo_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("repos.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="repo.indexed, repo.failed",
    )
    idempotency_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
        comment="Format: {repo_id}:{status}:{commit_hash}",
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relationships
    repo: Mapped["Repo"] = relationship("Repo", back_populates="webhook_deliveries")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'delivered', 'failed', 'exhausted')",
            name="valid_delivery_status",
        ),
        Index("idx_deliveries_status", "status"),
        Index(
            "idx_deliveries_next_retry",
            "next_retry_at",
            postgresql_where="status = 'pending'",
        ),
    )
