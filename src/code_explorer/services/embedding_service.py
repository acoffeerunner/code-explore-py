"""OpenAI embedding service with retry and batching."""

import hashlib
from typing import Any

import structlog
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError

from code_explorer.config import Settings, get_settings
from code_explorer.utils.langsmith_utils import create_openai_client
from code_explorer.utils.retry import with_async_retry

logger = structlog.get_logger(__name__)

# Maximum texts per embedding request (OpenAI limit is 2048)
MAX_BATCH_SIZE = 2048


class EmbeddingError(Exception):
    """Error generating embeddings."""

    pass


class EmbeddingService:
    """Service for generating text embeddings using OpenAI."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize embedding service."""
        self.settings = settings or get_settings()
        self._client: AsyncOpenAI | None = None
        self._cache: dict[str, list[float]] = {}  # In-memory cache by content hash

    @property
    def client(self) -> AsyncOpenAI:
        """Get or create OpenAI client (with optional LangSmith wrapping)."""
        if self._client is None:
            self._client = create_openai_client(
                self.settings.openai_api_key.get_secret_value(), self.settings,
            )
        return self._client

    def _get_cache_key(self, text: str) -> str:
        """Generate cache key for text content."""
        return hashlib.sha256(text.encode()).hexdigest()

    @with_async_retry(
        max_attempts=5,
        min_wait=1.0,
        max_wait=60.0,
        exceptions=(APIConnectionError, APITimeoutError, RateLimitError),
    )
    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for a batch of texts.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        response = await self.client.embeddings.create(
            input=texts,
            model=self.settings.embedding_model,
            dimensions=self.settings.embedding_dimensions,
        )

        # Sort by index to ensure correct ordering
        embeddings = sorted(response.data, key=lambda x: x.index)
        return [e.embedding for e in embeddings]

    async def embed_texts(
        self,
        texts: list[str],
        use_cache: bool = True,
    ) -> list[list[float]]:
        """
        Generate embeddings for multiple texts with caching and batching.

        Args:
            texts: List of texts to embed
            use_cache: Whether to use embedding cache

        Returns:
            List of embedding vectors (same order as input)
        """
        if not texts:
            return []

        log = logger.bind(text_count=len(texts))
        await log.ainfo("Generating embeddings")

        results: list[list[float] | None] = [None] * len(texts)
        texts_to_embed: list[tuple[int, str]] = []

        # Check cache for existing embeddings
        if use_cache:
            for i, text in enumerate(texts):
                cache_key = self._get_cache_key(text)
                if cache_key in self._cache:
                    results[i] = self._cache[cache_key]
                else:
                    texts_to_embed.append((i, text))
        else:
            texts_to_embed = list(enumerate(texts))

        cache_hits = len(texts) - len(texts_to_embed)
        if cache_hits > 0:
            await log.ainfo("Cache hits", hits=cache_hits)

        # Batch embed remaining texts
        if texts_to_embed:
            for batch_start in range(0, len(texts_to_embed), MAX_BATCH_SIZE):
                batch = texts_to_embed[batch_start : batch_start + MAX_BATCH_SIZE]
                batch_texts = [t[1] for t in batch]

                try:
                    embeddings = await self._embed_batch(batch_texts)

                    # Store results and update cache
                    for (original_idx, text), embedding in zip(batch, embeddings):
                        results[original_idx] = embedding
                        if use_cache:
                            cache_key = self._get_cache_key(text)
                            self._cache[cache_key] = embedding

                except Exception as e:
                    await log.aerror("Embedding batch failed", error=str(e))
                    raise EmbeddingError(f"Failed to generate embeddings: {e}") from e

        await log.ainfo("Embeddings generated", total=len(texts), cached=cache_hits)

        # Ensure all results are filled
        final_results: list[list[float]] = []
        for i, result in enumerate(results):
            if result is None:
                raise EmbeddingError(f"Missing embedding for text at index {i}")
            final_results.append(result)

        return final_results

    async def embed_single(self, text: str, use_cache: bool = True) -> list[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed
            use_cache: Whether to use embedding cache

        Returns:
            Embedding vector
        """
        results = await self.embed_texts([text], use_cache=use_cache)
        return results[0]

    def build_embed_text(
        self,
        content: str,
        file_path: str,
        symbol_type: str | None,
        symbol_name: str | None,
        language: str,
        docstring: str | None,
    ) -> str:
        """Build enriched text for embedding with structural header and docstring.

        The original content is prepended with metadata so the embedding model
        can encode structural context (file location, symbol identity, language).
        Docstrings are repeated for higher weight in the embedding.
        """
        parts: list[str] = []

        # Docstring (repeated for weight)
        if docstring:
            parts.append(f"# {docstring}")
            parts.append(f"# {docstring}")

        # Structural header
        header = f"# File: {file_path}"
        if symbol_type and symbol_name:
            header += f" | {symbol_type}: {symbol_name}"
        header += f" | Language: {language}"
        parts.append(header)

        # Original content
        parts.append(content)

        return "\n".join(parts)

    def clear_cache(self) -> None:
        """Clear the embedding cache."""
        self._cache.clear()
        logger.info("Embedding cache cleared")

    async def close(self) -> None:
        """Close the OpenAI client."""
        if self._client is not None:
            await self._client.close()
            self._client = None
