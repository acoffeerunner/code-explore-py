"""Pinecone vector database service for storing and querying embeddings."""

from uuid import UUID

import structlog
from pinecone import Pinecone, ServerlessSpec

from code_explorer.config import Settings, get_settings
from code_explorer.models.domain import PineconeMetadata, RetrievedChunk
from code_explorer.utils.retry import with_async_retry

logger = structlog.get_logger(__name__)

# Maximum vectors per upsert batch
MAX_UPSERT_BATCH = 100


class VectorError(Exception):
    """Error with vector operations."""

    pass


class VectorService:
    """Service for Pinecone vector database operations."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize vector service."""
        self.settings = settings or get_settings()
        self._client: Pinecone | None = None
        self._index = None

    @property
    def client(self) -> Pinecone:
        """Get or create Pinecone client."""
        if self._client is None:
            self._client = Pinecone(
                api_key=self.settings.pinecone_api_key.get_secret_value(),
            )
        return self._client

    @property
    def index(self):  # type: ignore[no-untyped-def]
        """Get or create Pinecone index."""
        if self._index is None:
            index_name = self.settings.pinecone_index_name

            # Check if index exists, create if not
            existing_indexes = [idx.name for idx in self.client.list_indexes()]

            if index_name not in existing_indexes:
                logger.info("Creating Pinecone index", index_name=index_name)
                self.client.create_index(
                    name=index_name,
                    dimension=self.settings.embedding_dimensions,
                    metric="cosine",
                    spec=ServerlessSpec(
                        cloud=self.settings.pinecone_cloud,
                        region=self.settings.pinecone_region,
                    ),
                )

            self._index = self.client.Index(index_name)
        return self._index

    def get_namespace(self, repo_id: UUID, commit_hash: str) -> str:
        """
        Generate namespace for versioned vector storage.

        Format: {repo_id}:{commit_hash}
        """
        return f"{repo_id}:{commit_hash}"

    @with_async_retry(max_attempts=3, min_wait=1.0, max_wait=30.0)
    async def upsert_vectors(
        self,
        namespace: str,
        vectors: list[tuple[str, list[float], PineconeMetadata]],
    ) -> int:
        """
        Upsert vectors to Pinecone in batches.

        Args:
            namespace: Pinecone namespace (repo_id:commit_hash)
            vectors: List of (id, embedding, metadata) tuples

        Returns:
            Number of vectors upserted
        """
        if not vectors:
            return 0

        log = logger.bind(namespace=namespace, vector_count=len(vectors))
        await log.ainfo("Upserting vectors")

        total_upserted = 0

        for batch_start in range(0, len(vectors), MAX_UPSERT_BATCH):
            batch = vectors[batch_start : batch_start + MAX_UPSERT_BATCH]

            # Format for Pinecone upsert
            records = [
                {
                    "id": vec_id,
                    "values": embedding,
                    "metadata": metadata.model_dump(),
                }
                for vec_id, embedding, metadata in batch
            ]

            try:
                self.index.upsert(vectors=records, namespace=namespace)
                total_upserted += len(batch)
            except Exception as e:
                await log.aerror("Upsert batch failed", error=str(e))
                raise VectorError(f"Failed to upsert vectors: {e}") from e

        await log.ainfo("Vectors upserted", count=total_upserted)
        return total_upserted

    @with_async_retry(max_attempts=3, min_wait=0.5, max_wait=10.0)
    async def query(
        self,
        namespace: str,
        vector: list[float],
        top_k: int = 15,
    ) -> list[dict]:
        """
        Query Pinecone for similar vectors.

        Args:
            namespace: Pinecone namespace to query
            vector: Query embedding vector
            top_k: Number of results to return

        Returns:
            List of matches with id, score, and metadata
        """
        log = logger.bind(namespace=namespace, top_k=top_k)
        await log.ainfo("Querying vectors")

        try:
            results = self.index.query(
                namespace=namespace,
                vector=vector,
                top_k=top_k,
                include_metadata=True,
            )

            matches = []
            for match in results.get("matches", []):
                matches.append(
                    {
                        "id": match["id"],
                        "score": match["score"],
                        "metadata": match.get("metadata", {}),
                    }
                )

            await log.ainfo("Query completed", match_count=len(matches))
            return matches

        except Exception as e:
            await log.aerror("Query failed", error=str(e))
            raise VectorError(f"Vector query failed: {e}") from e

    async def delete_namespace(self, namespace: str) -> None:
        """
        Delete all vectors in a namespace.

        Args:
            namespace: Namespace to delete
        """
        log = logger.bind(namespace=namespace)
        await log.ainfo("Deleting namespace")

        try:
            self.index.delete(delete_all=True, namespace=namespace)
            await log.ainfo("Namespace deleted")
        except Exception as e:
            await log.aerror("Delete failed", error=str(e))
            raise VectorError(f"Failed to delete namespace: {e}") from e

    async def get_namespace_stats(self, namespace: str) -> dict:
        """
        Get statistics for a namespace.

        Args:
            namespace: Namespace to check

        Returns:
            Dict with vector count and other stats
        """
        try:
            stats = self.index.describe_index_stats()
            namespaces = stats.get("namespaces", {})
            ns_stats = namespaces.get(namespace, {"vector_count": 0})
            return {"vector_count": ns_stats.get("vector_count", 0)}
        except Exception as e:
            logger.warning("Failed to get namespace stats", error=str(e))
            return {"vector_count": 0}

    def parse_retrieved_chunks(
        self,
        matches: list[dict],
        chunk_contents: dict[UUID, str],
    ) -> list[RetrievedChunk]:
        """
        Parse Pinecone matches into RetrievedChunk objects.

        Args:
            matches: Raw matches from Pinecone query
            chunk_contents: Dict mapping chunk_id to content (from Postgres)

        Returns:
            List of RetrievedChunk objects with full content
        """
        chunks: list[RetrievedChunk] = []

        for match in matches:
            chunk_id = UUID(match["id"])
            metadata = match.get("metadata", {})
            content = chunk_contents.get(chunk_id, "")

            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    score=match["score"],
                    file_path=metadata.get("file_path", ""),
                    start_line=metadata.get("start_line", 0),
                    end_line=metadata.get("end_line", 0),
                    symbol_name=metadata.get("symbol_name"),
                    symbol_type=metadata.get("symbol_type"),
                    language=metadata.get("language", "unknown"),
                    content=content,
                )
            )

        return chunks
