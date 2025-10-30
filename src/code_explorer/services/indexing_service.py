"""Indexing service that orchestrates the full indexing pipeline."""

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from code_explorer.models.db import Chunk, IndexVersion, Repo
from code_explorer.models.domain import CodeChunk, PineconeMetadata, RepoStatus, VersionStatus
from code_explorer.services.chunking_service import ChunkingService
from code_explorer.services.embedding_service import EmbeddingService
from code_explorer.services.git_service import GitService
from code_explorer.services.vector_service import VectorService

logger = structlog.get_logger(__name__)


class IndexingError(Exception):
    """Error during indexing pipeline."""

    pass


class IndexingService:
    """Service for orchestrating the complete indexing pipeline."""

    def __init__(
        self,
        git_service: GitService | None = None,
        chunking_service: ChunkingService | None = None,
        embedding_service: EmbeddingService | None = None,
        vector_service: VectorService | None = None,
    ) -> None:
        """Initialize indexing service with dependencies."""
        self.git = git_service or GitService()
        self.chunking = chunking_service or ChunkingService()
        self.embedding = embedding_service or EmbeddingService()
        self.vector = vector_service or VectorService()

    async def index_repository(
        self,
        db: AsyncSession,
        repo_id: UUID,
    ) -> None:
        """
        Execute the full indexing pipeline for a repository.

        Pipeline steps:
        1. Update status to 'cloning'
        2. Clone repository
        3. Check commit hash for idempotency
        4. Create new index version
        5. Update status to 'indexing'
        6. Scan for source files
        7. Chunk files with tree-sitter
        8. Store chunks in Postgres
        9. Generate embeddings
        10. Upsert to Pinecone
        11. Swap active version
        12. Cleanup and update status

        Args:
            db: Database session
            repo_id: Repository UUID to index
        """
        log = logger.bind(repo_id=str(repo_id))
        await log.ainfo("Starting indexing pipeline")

        # Fetch repo
        result = await db.execute(select(Repo).where(Repo.id == repo_id))
        repo = result.scalar_one_or_none()

        if not repo:
            await log.aerror("Repository not found")
            raise IndexingError(f"Repository {repo_id} not found")

        try:
            # Step 1: Update status to cloning
            await self._update_repo_status(db, repo, RepoStatus.CLONING)

            # Step 2: Clone repository
            await log.ainfo("Cloning repository", url=repo.url, branch=repo.branch)
            clone_path, commit_hash = await self.git.clone_repo(
                url=repo.url,
                branch=repo.branch,
                repo_id=repo_id,
            )

            # Step 3: Check for existing version with same commit
            existing_version = await db.execute(
                select(IndexVersion).where(
                    IndexVersion.repo_id == repo_id,
                    IndexVersion.commit_hash == commit_hash,
                    IndexVersion.status == VersionStatus.ACTIVE.value,
                )
            )
            if existing_version.scalar_one_or_none():
                await log.ainfo("Version already indexed", commit_hash=commit_hash)
                await self._update_repo_status(db, repo, RepoStatus.READY)
                self.git.cleanup_clone(repo_id)
                return

            # Step 4: Create new index version
            namespace = self.vector.get_namespace(repo_id, commit_hash)
            version = IndexVersion(
                repo_id=repo_id,
                commit_hash=commit_hash,
                pinecone_namespace=namespace,
                status=VersionStatus.BUILDING.value,
            )
            db.add(version)
            await db.flush()  # Get version.id

            await log.ainfo("Created index version", version_id=str(version.id))

            # Step 5: Update status to indexing
            await self._update_repo_status(db, repo, RepoStatus.INDEXING)

            # Step 6: Scan for source files
            files = await self.chunking.scan_directory(clone_path)
            total_files = len(files)
            await log.ainfo("Found source files", count=total_files)

            # Step 7-10: Process files in batches
            all_chunks: list[CodeChunk] = []
            processed_files = 0

            for file_path in files:
                relative_path = str(file_path.relative_to(clone_path))

                # Chunk file
                chunks = await self.chunking.chunk_file(
                    file_path=file_path,
                    repo_id=repo_id,
                    version_id=version.id,
                    relative_path=relative_path,
                )
                all_chunks.extend(chunks)
                processed_files += 1

                # Update progress
                progress = int((processed_files / total_files) * 50)  # First 50% for chunking
                await self._update_progress(db, repo, progress)

            await log.ainfo("Chunking complete", chunk_count=len(all_chunks))

            # Step 8: Store chunks in Postgres
            for chunk in all_chunks:
                db_chunk = Chunk(
                    id=chunk.id,
                    version_id=chunk.version_id,
                    repo_id=chunk.repo_id,
                    file_path=chunk.file_path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    symbol_name=chunk.symbol_name,
                    symbol_type=chunk.symbol_type.value if chunk.symbol_type else None,
                    language=chunk.language.value,
                    content=chunk.content,
                    content_hash=chunk.content_hash,
                    token_count=chunk.token_count,
                )
                db.add(db_chunk)

            await db.flush()
            await log.ainfo("Chunks stored in database")

            # Step 9: Generate embeddings (batched) — with structural headers
            embed_texts = [
                self.embedding.build_embed_text(
                    content=c.content,
                    file_path=c.file_path,
                    symbol_type=c.symbol_type.value if c.symbol_type else None,
                    symbol_name=c.symbol_name,
                    language=c.language.value,
                    docstring=c.docstring,
                )
                for c in all_chunks
            ]
            embeddings = await self.embedding.embed_texts(embed_texts)

            await log.ainfo("Embeddings generated")
            await self._update_progress(db, repo, 75)

            # Step 10: Upsert to Pinecone
            vectors: list[tuple[str, list[float], PineconeMetadata]] = []
            for chunk, embedding in zip(all_chunks, embeddings):
                metadata = PineconeMetadata(
                    file_path=chunk.file_path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    symbol_name=chunk.symbol_name,
                    symbol_type=chunk.symbol_type.value if chunk.symbol_type else None,
                    language=chunk.language.value,
                )
                vectors.append((str(chunk.id), embedding, metadata))

            await self.vector.upsert_vectors(namespace, vectors)
            await log.ainfo("Vectors upserted to Pinecone")
            await self._update_progress(db, repo, 90)

            # Step 11: Swap active version
            old_version_id = repo.active_version_id

            # Mark old version as stale
            if old_version_id:
                await db.execute(
                    update(IndexVersion)
                    .where(IndexVersion.id == old_version_id)
                    .values(status=VersionStatus.STALE.value)
                )

            # Mark new version as active
            version.status = VersionStatus.ACTIVE.value
            version.completed_at = datetime.now(UTC)
            version.chunk_count = len(all_chunks)
            version.file_count = total_files

            # Update repo with new active version
            repo.active_version_id = version.id
            repo.file_count = total_files
            repo.chunk_count = len(all_chunks)
            repo.indexed_at = datetime.now(UTC)

            # Step 12: Cleanup and finalize
            self.git.cleanup_clone(repo_id)
            await self._update_repo_status(db, repo, RepoStatus.READY, progress=100)

            await db.commit()
            await log.ainfo(
                "Indexing complete",
                file_count=total_files,
                chunk_count=len(all_chunks),
            )

        except Exception as e:
            await log.aerror("Indexing failed", error=str(e))
            await db.rollback()

            # Update repo with error
            await self._update_repo_status(
                db,
                repo,
                RepoStatus.FAILED,
                error_message=str(e),
            )
            await db.commit()

            # Cleanup
            self.git.cleanup_clone(repo_id)
            raise IndexingError(f"Indexing failed: {e}") from e

    async def _update_repo_status(
        self,
        db: AsyncSession,
        repo: Repo,
        status: RepoStatus,
        progress: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update repository status."""
        repo.status = status.value
        if progress is not None:
            repo.progress_pct = progress
        if error_message is not None:
            repo.error_message = error_message
        repo.updated_at = datetime.now(UTC)
        await db.flush()

    async def _update_progress(
        self,
        db: AsyncSession,
        repo: Repo,
        progress: int,
    ) -> None:
        """Update repository progress percentage."""
        repo.progress_pct = progress
        await db.flush()
