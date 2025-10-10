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
        Field(default=1536, description="Embedding vector dimensions"),
    ]
    default_chat_model: Annotated[
        str,
        Field(default="gpt-4o-mini", description="Default chat model for RAG"),
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
    allowed_git_hosts: Annotated[
        list[str],
        Field(
            default=["github.com", "gitlab.com", "bitbucket.org", "codeberg.org"],
            description="Allowed Git hosts for cloning",
        ),
    ]

    @field_validator("allowed_git_hosts", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, v: str | list[str]) -> list[str]:
        """Parse comma-separated string into list."""
        if isinstance(v, str):
            return [host.strip() for host in v.split(",") if host.strip()]
        return v

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
