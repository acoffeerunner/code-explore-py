"""Application configuration using Pydantic Settings."""

from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # ==========================================================================
    # Supabase Configuration
    # ==========================================================================
    supabase_url: Annotated[
        str,
        Field(description="Supabase project URL (e.g., https://xxx.supabase.co)"),
    ]
    supabase_anon_key: Annotated[
        SecretStr,
        Field(description="Supabase anon/public key"),
    ]
    supabase_service_role_key: Annotated[
        SecretStr,
        Field(description="Supabase service role key (for admin operations)"),
    ]
    supabase_jwt_secret: Annotated[
        SecretStr,
        Field(description="Supabase JWT secret for token verification"),
    ]
    database_url: Annotated[
        str,
        Field(description="PostgreSQL connection URL (asyncpg format)"),
    ]

    # ==========================================================================
    # Redis Configuration
    # ==========================================================================
    redis_url: Annotated[
        str,
        Field(default="redis://localhost:6379/0", description="Redis connection URL"),
    ]

    # ==========================================================================
    # Pinecone Configuration
    # ==========================================================================
    pinecone_api_key: Annotated[
        SecretStr,
        Field(description="Pinecone API key"),
    ]
    pinecone_index_name: Annotated[
        str,
        Field(default="code-explorer", description="Pinecone index name"),
    ]
    pinecone_cloud: Annotated[
        str,
        Field(default="aws", description="Pinecone cloud provider"),
    ]
    pinecone_region: Annotated[
        str,
        Field(default="us-east-1", description="Pinecone region"),
    ]

    # ==========================================================================
    # OpenAI Configuration
    # ==========================================================================
    openai_api_key: Annotated[
        SecretStr,
        Field(description="OpenAI API key"),
    ]
    embedding_model: Annotated[
        str,
        Field(default="text-embedding-3-large", description="OpenAI embedding model"),
    ]
    embedding_dimensions: Annotated[
        int,
        Field(default=3072, description="Embedding vector dimensions"),
    ]
    default_chat_model: Annotated[
        str,
        Field(default="gpt-4o-mini", description="Default chat model for RAG"),
    ]

    # ==========================================================================
    # RAG Pipeline Configuration
    # ==========================================================================
    reranker_model: Annotated[
        str,
        Field(default="gpt-4o-mini", description="Model used for LLM-based reranking"),
    ]
    hyde_model: Annotated[
        str,
        Field(default="gpt-4o-mini", description="Model used for HyDE generation"),
    ]
    hyde_enabled: Annotated[
        bool,
        Field(default=True, description="Enable HyDE (Hypothetical Document Embedding)"),
    ]
    reranker_enabled: Annotated[
        bool,
        Field(default=True, description="Enable LLM-based reranking"),
    ]
    min_similarity_score: Annotated[
        float,
        Field(default=0.3, ge=0.0, le=1.0, description="Minimum vector similarity threshold"),
    ]
    chat_history_turns: Annotated[
        int,
        Field(default=5, ge=0, le=20, description="Number of previous chat turns sent for context"),
    ]

    # ==========================================================================
    # LangSmith Configuration
    # ==========================================================================
    langsmith_api_key: Annotated[
        SecretStr | None,
        Field(default=None, description="LangSmith API key (enables tracing when set)"),
    ]
    langsmith_project: Annotated[
        str,
        Field(default="code-explorer", description="LangSmith project name"),
    ]

    # ==========================================================================
    # Git Configuration
    # ==========================================================================
    git_clone_timeout: Annotated[
        int,
        Field(default=120, ge=30, le=600, description="Git clone timeout in seconds"),
    ]
    max_repo_size_mb: Annotated[
        int,
        Field(default=250, ge=10, le=1000, description="Maximum repository size in MB"),
    ]
    clone_base_path: Annotated[
        Path,
        Field(default=Path("/tmp/repos"), description="Base path for cloned repositories"),
    ]
    # Note: Use comma-separated string in .env, parsed by validator below
    allowed_git_hosts_str: Annotated[
        str,
        Field(
            default="github.com,gitlab.com,bitbucket.org,codeberg.org",
            alias="ALLOWED_GIT_HOSTS",
            description="Allowed Git hosts for cloning (comma-separated)",
        ),
    ]

    @property
    def allowed_git_hosts(self) -> list[str]:
        """Get allowed Git hosts as a list."""
        return [host.strip() for host in self.allowed_git_hosts_str.split(",") if host.strip()]

    # ==========================================================================
    # Rate Limiting Configuration (Dual-Layer)
    # ==========================================================================
    rate_limit_per_user: Annotated[
        int,
        Field(default=100, ge=1, description="Max requests per user per window"),
    ]
    rate_limit_per_ip: Annotated[
        int,
        Field(default=1000, ge=1, description="Max requests per IP per window"),
    ]
    rate_limit_window_seconds: Annotated[
        int,
        Field(default=60, ge=1, description="Rate limit window in seconds"),
    ]

    # ==========================================================================
    # Security Configuration
    # ==========================================================================
    trusted_proxy_count: Annotated[
        int,
        Field(
            default=0,
            ge=0,
            description="Number of trusted proxies for X-Forwarded-For extraction",
        ),
    ]

    # ==========================================================================
    # Webhook Configuration
    # ==========================================================================
    webhook_max_retries: Annotated[
        int,
        Field(default=6, ge=1, le=10, description="Max webhook delivery retries"),
    ]
    webhook_retry_window_hours: Annotated[
        int,
        Field(default=1, ge=1, description="Webhook retry window in hours"),
    ]

    # ==========================================================================
    # Garbage Collection Configuration
    # ==========================================================================
    stale_version_ttl_hours: Annotated[
        int,
        Field(default=1, ge=1, description="Hours to keep stale versions before GC"),
    ]

    # ==========================================================================
    # Observability Configuration
    # ==========================================================================
    otlp_endpoint: Annotated[
        str | None,
        Field(default=None, description="OpenTelemetry OTLP endpoint"),
    ]
    log_level: Annotated[
        str,
        Field(default="INFO", description="Logging level"),
    ]
    log_format: Annotated[
        str,
        Field(default="json", description="Log format: 'json' or 'console'"),
    ]

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level is valid."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid_levels:
            msg = f"Invalid log level: {v}. Must be one of {valid_levels}"
            raise ValueError(msg)
        return upper

    @field_validator("log_format")
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        """Validate log format is valid."""
        valid_formats = {"json", "console"}
        lower = v.lower()
        if lower not in valid_formats:
            msg = f"Invalid log format: {v}. Must be one of {valid_formats}"
            raise ValueError(msg)
        return lower


# Global settings instance (lazy-loaded)
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the global settings instance (cached)."""
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
