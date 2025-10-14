"""RAG-based chat service for answering questions about code."""

import re
from uuid import UUID

import structlog
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from code_explorer.config import Settings, get_settings
from code_explorer.models.db import Chunk, IndexVersion, Repo
from code_explorer.models.domain import RetrievedChunk, SourceCitation, TokenUsage
from code_explorer.models.responses import ChatResponse
from code_explorer.services.embedding_service import EmbeddingService
from code_explorer.services.vector_service import VectorService

logger = structlog.get_logger(__name__)

# Maximum context window size in tokens
MAX_CONTEXT_TOKENS = 6000


class ChatError(Exception):
    """Error during chat processing."""

    pass


class ChatService:
    """Service for RAG-based code Q&A."""

    def __init__(
        self,
        settings: Settings | None = None,
        embedding_service: EmbeddingService | None = None,
        vector_service: VectorService | None = None,
    ) -> None:
        """Initialize chat service."""
        self.settings = settings or get_settings()
        self.embedding = embedding_service or EmbeddingService()
        self.vector = vector_service or VectorService()
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        """Get or create OpenAI client."""
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.settings.openai_api_key.get_secret_value(),
            )
        return self._client

    async def query(
        self,
        db: AsyncSession,
        repo_id: UUID,
        user_id: UUID,
        question: str,
        model: str | None = None,
        top_k: int = 15,
    ) -> ChatResponse:
        """
        Answer a question about code using RAG.

        Pipeline:
        1. Verify user owns the repo
        2. Get active index version namespace
        3. Embed the question
        4. Query Pinecone for relevant chunks
        5. Fetch chunk content from Postgres
        6. Build context window
        7. Call LLM with structured prompt
        8. Validate and extract citations
        9. Return structured response

        Args:
            db: Database session
            repo_id: Repository UUID
            user_id: Authenticated user UUID
            question: User's question
            model: Optional LLM model override
            top_k: Number of chunks to retrieve

        Returns:
            ChatResponse with answer, sources, and usage
        """
        log = logger.bind(repo_id=str(repo_id), question_length=len(question))
        await log.ainfo("Processing chat query")

        # Step 1: Verify ownership and get repo
        result = await db.execute(
            select(Repo).where(Repo.id == repo_id, Repo.user_id == user_id)
        )
        repo = result.scalar_one_or_none()

        if not repo:
            raise ChatError("Repository not found or access denied")

        if not repo.active_version_id:
            raise ChatError("Repository has not been indexed yet")

        # Get active version
        version_result = await db.execute(
            select(IndexVersion).where(IndexVersion.id == repo.active_version_id)
        )
        version = version_result.scalar_one_or_none()

        if not version:
            raise ChatError("Index version not found")

        # Step 2: Get namespace
        namespace = version.pinecone_namespace

        # Step 3: Embed question
        question_embedding = await self.embedding.embed_single(question)

        # Step 4: Query Pinecone
        matches = await self.vector.query(
            namespace=namespace,
            vector=question_embedding,
            top_k=top_k,
        )

        if not matches:
            await log.ainfo("No relevant chunks found")
            return ChatResponse(
                answer="I couldn't find any relevant code to answer your question. "
                "Please make sure the repository has been indexed and try rephrasing your question.",
                sources=[],
                model=model or self.settings.default_chat_model,
                usage=TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            )

        # Step 5: Fetch chunk content from Postgres
        chunk_ids = [UUID(m["id"]) for m in matches]
        chunks_result = await db.execute(
            select(Chunk).where(Chunk.id.in_(chunk_ids))
        )
        db_chunks = {c.id: c for c in chunks_result.scalars().all()}

        # Build retrieved chunks with content
        retrieved_chunks: list[RetrievedChunk] = []
        for match in matches:
            chunk_id = UUID(match["id"])
            if chunk_id in db_chunks:
                db_chunk = db_chunks[chunk_id]
                retrieved_chunks.append(
                    RetrievedChunk(
                        chunk_id=chunk_id,
                        score=match["score"],
                        file_path=db_chunk.file_path,
                        start_line=db_chunk.start_line,
                        end_line=db_chunk.end_line,
                        symbol_name=db_chunk.symbol_name,
                        symbol_type=db_chunk.symbol_type,
                        language=db_chunk.language,
                        content=db_chunk.content,
                    )
                )

        # Step 6: Build context window
        context, chunk_map = self._build_context(retrieved_chunks)

        # Step 7: Call LLM
        model_name = model or self.settings.default_chat_model
        system_prompt = self._build_system_prompt(repo.url, repo.branch)
        user_prompt = self._build_user_prompt(context, question)

        await log.ainfo(
            "Calling LLM",
            model=model_name,
            context_chunks=len(retrieved_chunks),
        )

        response = await self.client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=2000,
        )

        answer = response.choices[0].message.content or ""
        usage = TokenUsage(
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=response.usage.completion_tokens if response.usage else 0,
            total_tokens=response.usage.total_tokens if response.usage else 0,
        )

        # Step 8: Validate and extract citations
        sources = self._extract_citations(answer, chunk_map)

        await log.ainfo(
            "Chat query completed",
            answer_length=len(answer),
            source_count=len(sources),
        )

        return ChatResponse(
            answer=answer,
            sources=sources,
            model=model_name,
            usage=usage,
        )

    def _build_context(
        self,
        chunks: list[RetrievedChunk],
    ) -> tuple[str, dict[int, RetrievedChunk]]:
        """
        Build context string from retrieved chunks.

        Returns:
            Tuple of (context_string, citation_index_to_chunk_map)
        """
        context_parts: list[str] = []
        chunk_map: dict[int, RetrievedChunk] = {}
        total_tokens = 0

        for i, chunk in enumerate(chunks, start=1):
            # Estimate tokens (rough approximation)
            chunk_tokens = len(chunk.content.split()) * 1.3

            if total_tokens + chunk_tokens > MAX_CONTEXT_TOKENS:
                break

            symbol_info = ""
            if chunk.symbol_name:
                symbol_info = f" ({chunk.symbol_name})"

            header = f"[{i}] {chunk.file_path}:{chunk.start_line}-{chunk.end_line}{symbol_info}"
            context_parts.append(f"{header}\n```{chunk.language}\n{chunk.content}\n```")

            chunk_map[i] = chunk
            total_tokens += chunk_tokens

        return "\n\n".join(context_parts), chunk_map

    def _build_system_prompt(self, repo_url: str, branch: str) -> str:
        """Build system prompt for the LLM."""
        return f"""You are a helpful code assistant for the repository at {repo_url} (branch: {branch}).

You have access to code chunks from this repository, provided in the context below.
When answering questions:
1. Reference specific code using citations in brackets: [1], [2], etc.
2. Only cite chunks that are provided in the context
3. Be specific about file locations and line numbers
4. If you're unsure or the context doesn't contain relevant information, say so
5. Focus on explaining the code clearly and accurately

Important: Do not make up or hallucinate code. Only reference code that is explicitly shown in the context."""

    def _build_user_prompt(self, context: str, question: str) -> str:
        """Build user prompt with context and question."""
        return f"""Context (code chunks from the repository, numbered for citation):

{context}

---

Question: {question}

Please answer the question based on the code context above. Use citations [1], [2], etc. when referencing specific code chunks."""

    def _extract_citations(
        self,
        answer: str,
        chunk_map: dict[int, RetrievedChunk],
    ) -> list[SourceCitation]:
        """
        Extract and validate citations from the answer.

        Args:
            answer: LLM response text
            chunk_map: Map of citation index to chunk

        Returns:
            List of validated SourceCitation objects
        """
        # Find all citation patterns [N]
        citation_pattern = r"\[(\d+)\]"
        found_citations = set(int(m) for m in re.findall(citation_pattern, answer))

        sources: list[SourceCitation] = []
        seen_chunks: set[UUID] = set()

        for citation_idx in sorted(found_citations):
            if citation_idx not in chunk_map:
                # Invalid citation - log warning
                logger.warning(
                    "Invalid citation in response",
                    citation=citation_idx,
                    valid_range=f"1-{len(chunk_map)}",
                )
                continue

            chunk = chunk_map[citation_idx]

            # Deduplicate by chunk_id
            if chunk.chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk.chunk_id)

            sources.append(
                SourceCitation(
                    chunk_id=chunk.chunk_id,
                    file=chunk.file_path,
                    line_start=chunk.start_line,
                    line_end=chunk.end_line,
                    symbol_name=chunk.symbol_name,
                    score=chunk.score,
                )
            )

        return sources

    async def close(self) -> None:
        """Close the OpenAI client."""
        if self._client is not None:
            await self._client.close()
            self._client = None
