"""RAG-based chat service for answering questions about code."""

import json
import re
import time
from uuid import UUID

import structlog
import tiktoken
from langsmith import traceable
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from code_explorer.config import Settings, get_settings
from code_explorer.models.db import Chunk, IndexVersion, Repo
from code_explorer.models.domain import RetrievedChunk, SourceCitation, TokenUsage
from code_explorer.models.metrics import PipelineMetrics
from code_explorer.models.responses import ChatResponse
from code_explorer.services.embedding_service import EmbeddingService
from code_explorer.services.query_service import QueryService
from code_explorer.services.vector_service import VectorService
from code_explorer.utils.langsmith_utils import create_openai_client

logger = structlog.get_logger(__name__)

# Maximum context window size in tokens
MAX_CONTEXT_TOKENS = 12_000
# Minimum chunks to keep even if below score threshold
MIN_RESULTS_FLOOR = 3


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
        query_service: QueryService | None = None,
    ) -> None:
        """Initialize chat service."""
        self.settings = settings or get_settings()
        self.embedding = embedding_service or EmbeddingService()
        self.vector = vector_service or VectorService()
        self.query_svc = query_service or QueryService(settings=self.settings)
        self._client: AsyncOpenAI | None = None
        self._tokenizer = tiktoken.get_encoding("cl100k_base")

    @property
    def client(self) -> AsyncOpenAI:
        """Get or create OpenAI client (with optional LangSmith wrapping)."""
        if self._client is None:
            self._client = create_openai_client(
                self.settings.openai_api_key.get_secret_value(), self.settings,
            )
        return self._client

    @traceable(name="rag_retrieval", run_type="chain")
    async def query_retrieval(
        self,
        db: AsyncSession,
        repo_id: UUID,
        user_id: UUID,
        question: str,
        model: str | None = None,
        top_k: int = 15,
        history: list[dict[str, str]] | None = None,
    ) -> dict:
        """Run the retrieval pipeline and return prompts + chunk_map for streaming or sync use."""
        log = logger.bind(repo_id=str(repo_id), question_length=len(question))
        await log.ainfo("Processing chat query (retrieval phase)")

        metrics = PipelineMetrics()

        # Step 1: Verify ownership and get repo
        result = await db.execute(select(Repo).where(Repo.id == repo_id, Repo.user_id == user_id))
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

        namespace = version.pinecone_namespace

        # Step 2: Chat history rewrite
        effective_question = question
        if history:
            t0 = time.perf_counter()
            effective_question = await self.query_svc.rewrite_with_history(question, history)
            ms = round((time.perf_counter() - t0) * 1000)
            metrics.history_rewrite_used = True
            metrics.latency_history_rewrite_ms = ms
            await log.ainfo("History rewrite complete", duration_ms=ms, rewritten=effective_question[:100])
        metrics.effective_question = effective_question if effective_question != question else None

        # Step 3: Keyword + metadata extraction
        keywords = self.query_svc.extract_keywords(effective_question)
        metadata_filters = self.query_svc.extract_metadata_filters(effective_question)
        await log.ainfo("Query analysis complete", keywords=keywords, filters=metadata_filters)

        # Step 4: HyDE
        embed_text = effective_question
        if self.settings.hyde_enabled:
            t0 = time.perf_counter()
            embed_text = await self.query_svc.generate_hyde(effective_question)
            ms = round((time.perf_counter() - t0) * 1000)
            metrics.hyde_used = True
            metrics.latency_hyde_ms = ms
            await log.ainfo("HyDE generation complete", duration_ms=ms)

        # Step 5: Dense search (Pinecone with metadata filters)
        t0 = time.perf_counter()
        question_embedding = await self.embedding.embed_single(embed_text)
        metrics.latency_embedding_ms = round((time.perf_counter() - t0) * 1000)
        await log.ainfo("Embedding complete", duration_ms=metrics.latency_embedding_ms)

        pinecone_filter = self._build_pinecone_filter(metadata_filters)
        t0 = time.perf_counter()
        dense_matches = await self.vector.query(
            namespace=namespace, vector=question_embedding, top_k=top_k, filter=pinecone_filter,
        )
        metrics.latency_dense_search_ms = round((time.perf_counter() - t0) * 1000)
        metrics.dense_result_count = len(dense_matches)
        if dense_matches:
            metrics.top_dense_score = dense_matches[0].get("score", 0.0)

        # Step 6: Sparse search (Postgres FTS with extracted keywords)
        t0 = time.perf_counter()
        sparse_matches = await self._fts_search(db, version.id, keywords, limit=top_k)
        metrics.latency_sparse_search_ms = round((time.perf_counter() - t0) * 1000)
        metrics.sparse_result_count = len(sparse_matches)

        # Step 7: RRF fusion
        merged = self._reciprocal_rank_fusion(dense_matches, sparse_matches)
        metrics.merged_result_count = len(merged)
        if merged:
            metrics.top_rrf_score = merged[0].get("rrf_score", 0.0)
        await log.ainfo(
            "Search complete",
            dense_count=len(dense_matches),
            sparse_count=len(sparse_matches),
            merged_count=len(merged),
            dense_ms=metrics.latency_dense_search_ms,
            sparse_ms=metrics.latency_sparse_search_ms,
        )

        model_name = model or self.settings.default_chat_model

        if not merged:
            await log.ainfo("No relevant chunks found")
            metrics.no_results = True
            return {
                "system_prompt": "",
                "user_prompt": "",
                "chunk_map": {},
                "model_name": model_name,
                "no_results": True,
                "metrics": metrics,
            }

        # Step 8: Fetch chunk content from Postgres
        chunk_ids = [UUID(m["id"]) for m in merged[:top_k]]
        chunks_result = await db.execute(select(Chunk).where(Chunk.id.in_(chunk_ids)))
        db_chunks = {c.id: c for c in chunks_result.scalars().all()}

        retrieved_chunks: list[RetrievedChunk] = []
        for match in merged[:top_k]:
            chunk_id = UUID(match["id"])
            if chunk_id in db_chunks:
                db_chunk = db_chunks[chunk_id]
                # Use the original dense cosine score for threshold filtering;
                # RRF scores (~0.01-0.03) are not on the same scale.
                dense_score = match.get("score", 0.0)
                retrieved_chunks.append(
                    RetrievedChunk(
                        chunk_id=chunk_id,
                        score=dense_score,
                        file_path=db_chunk.file_path,
                        start_line=db_chunk.start_line,
                        end_line=db_chunk.end_line,
                        symbol_name=db_chunk.symbol_name,
                        symbol_type=db_chunk.symbol_type,
                        language=db_chunk.language,
                        content=db_chunk.content,
                    )
                )

        # Step 9: Score threshold filtering (uses dense cosine score, not RRF score)
        # Keep at least MIN_RESULTS_FLOOR chunks even if below threshold, so the
        # user never gets an empty answer when Pinecone returned matches.
        pre_filter = list(retrieved_chunks)
        retrieved_chunks = self._apply_score_threshold(
            retrieved_chunks, min_score=self.settings.min_similarity_score
        )
        if len(retrieved_chunks) < MIN_RESULTS_FLOOR and pre_filter:
            retrieved_chunks = sorted(pre_filter, key=lambda c: c.score, reverse=True)[:MIN_RESULTS_FLOOR]
        metrics.post_threshold_count = len(retrieved_chunks)

        # Step 10: Parent chunk expansion
        t0 = time.perf_counter()
        parent_chunks = await self._fetch_parent_chunks(db, retrieved_chunks, version.id)
        metrics.parent_chunks_added = len(parent_chunks)
        await log.ainfo("Parent expansion complete", duration_ms=round((time.perf_counter() - t0) * 1000), parent_count=len(parent_chunks))

        # Step 11: LLM reranking (if enabled)
        if self.settings.reranker_enabled:
            t0 = time.perf_counter()
            retrieved_chunks = await self._rerank_chunks(retrieved_chunks, effective_question)
            ms = round((time.perf_counter() - t0) * 1000)
            metrics.reranker_used = True
            metrics.latency_rerank_ms = ms
            await log.ainfo("Reranking complete", duration_ms=ms)

        # Step 12: Build context with tiktoken
        context, chunk_map = self._build_context(retrieved_chunks)
        parent_context = ""
        if parent_chunks:
            parent_context, _ = self._build_context(parent_chunks)

        system_prompt = self._build_system_prompt(repo.url, repo.branch)
        user_prompt = self._build_user_prompt(context, parent_context, effective_question)

        await log.ainfo(
            "Retrieval phase complete",
            model=model_name,
            context_chunks=len(retrieved_chunks),
            parent_chunks=len(parent_chunks),
        )

        return {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "chunk_map": chunk_map,
            "model_name": model_name,
            "no_results": False,
            "metrics": metrics,
        }

    @traceable(name="rag_query", run_type="chain")
    async def query(
        self,
        db: AsyncSession,
        repo_id: UUID,
        user_id: UUID,
        question: str,
        model: str | None = None,
        top_k: int = 15,
        history: list[dict[str, str]] | None = None,
    ) -> ChatResponse:
        """Answer a question about code using the enhanced RAG pipeline."""
        t_total = time.perf_counter()

        retrieval = await self.query_retrieval(
            db, repo_id, user_id, question, model, top_k, history,
        )
        metrics: PipelineMetrics = retrieval["metrics"]

        if retrieval["no_results"]:
            total_ms = round((time.perf_counter() - t_total) * 1000)
            await self._log_query(
                db, repo_id, user_id, question, metrics,
                model=retrieval["model_name"], answer_length=0,
                citation_count=0, prompt_tokens=0, completion_tokens=0,
                latency_llm_ms=None, latency_total_ms=total_ms,
            )
            return ChatResponse(
                answer="I couldn't find any relevant code to answer your question. "
                "Please make sure the repository has been indexed and try rephrasing your question.",
                sources=[],
                model=retrieval["model_name"],
                usage=TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            )

        log = logger.bind(repo_id=str(repo_id))
        await log.ainfo("Calling LLM", model=retrieval["model_name"])

        t_llm = time.perf_counter()
        response = await self.client.chat.completions.create(
            model=retrieval["model_name"],
            messages=[
                {"role": "system", "content": retrieval["system_prompt"]},
                {"role": "user", "content": retrieval["user_prompt"]},
            ],
            max_completion_tokens=4000,
        )
        llm_ms = round((time.perf_counter() - t_llm) * 1000)

        answer = response.choices[0].message.content or ""
        usage = TokenUsage(
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=response.usage.completion_tokens if response.usage else 0,
            total_tokens=response.usage.total_tokens if response.usage else 0,
        )

        sources = self._extract_citations(answer, retrieval["chunk_map"])
        total_ms = round((time.perf_counter() - t_total) * 1000)

        await self._log_query(
            db, repo_id, user_id, question, metrics,
            model=retrieval["model_name"], answer_length=len(answer),
            citation_count=len(sources),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            latency_llm_ms=llm_ms, latency_total_ms=total_ms,
        )

        await log.ainfo(
            "Chat query completed",
            answer_length=len(answer),
            source_count=len(sources),
        )

        return ChatResponse(
            answer=answer,
            sources=sources,
            model=retrieval["model_name"],
            usage=usage,
        )

    async def _stream_llm_response(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ):
        """Yield SSE events from a streaming OpenAI chat completion."""
        stream = await self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_completion_tokens=4000,
            stream=True,
        )

        full_content = ""
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                full_content += delta.content
                yield {"event": "token", "data": {"content": delta.content}}

        yield {"event": "content_done", "data": {"full_content": full_content}}

    def _build_pinecone_filter(self, metadata_filters: dict[str, str]) -> dict | None:
        """Convert extracted metadata filters to Pinecone filter format."""
        if not metadata_filters:
            return None

        pinecone_filter: dict = {}
        if "language" in metadata_filters:
            pinecone_filter["language"] = {"$eq": metadata_filters["language"]}
        if "symbol_type" in metadata_filters:
            pinecone_filter["symbol_type"] = {"$eq": metadata_filters["symbol_type"]}

        return pinecone_filter if pinecone_filter else None

    async def _fetch_parent_chunks(
        self,
        db: AsyncSession,
        chunks: list[RetrievedChunk],
        version_id: UUID,
    ) -> list[RetrievedChunk]:
        """Fetch parent class chunks for method-type retrieved chunks."""
        from sqlalchemy import text

        parent_chunks: list[RetrievedChunk] = []
        seen_parents: set[str] = set()

        for chunk in chunks:
            if chunk.symbol_type != "method":
                continue

            parent_key = f"{chunk.file_path}:{chunk.start_line}"
            if parent_key in seen_parents:
                continue

            result = await db.execute(
                text(
                    "SELECT id, file_path, start_line, end_line, symbol_name, symbol_type, "
                    "language, content FROM chunks "
                    "WHERE version_id = :vid AND file_path = :path "
                    "AND symbol_type = 'class' "
                    "AND start_line <= :method_start AND end_line >= :method_end "
                    "LIMIT 1"
                ),
                {
                    "vid": version_id,
                    "path": chunk.file_path,
                    "method_start": chunk.start_line,
                    "method_end": chunk.end_line,
                },
            )
            row = result.fetchone()
            if row:
                seen_parents.add(parent_key)
                parent_chunks.append(
                    RetrievedChunk(
                        chunk_id=row.id,
                        score=0.0,
                        file_path=row.file_path,
                        start_line=row.start_line,
                        end_line=row.end_line,
                        symbol_name=row.symbol_name,
                        symbol_type=row.symbol_type,
                        language=row.language,
                        content=row.content,
                    )
                )

        return parent_chunks

    def _apply_score_threshold(
        self,
        chunks: list[RetrievedChunk],
        min_score: float,
    ) -> list[RetrievedChunk]:
        """Filter out chunks below the minimum similarity score."""
        return [c for c in chunks if c.score >= min_score]

    def _build_context(
        self,
        chunks: list[RetrievedChunk],
    ) -> tuple[str, dict[int, RetrievedChunk]]:
        """Build context string from retrieved chunks using tiktoken for token counting."""
        context_parts: list[str] = []
        chunk_map: dict[int, RetrievedChunk] = {}
        total_tokens = 0

        for i, chunk in enumerate(chunks, start=1):
            chunk_tokens = len(self._tokenizer.encode(chunk.content))

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
        return f"""You are a code assistant for the repository at {repo_url} (branch: {branch}).

You answer questions using code chunks provided in the context below. Follow these rules:

1. Reference specific code using citations: [1], [2], etc.
2. Only cite chunks from the provided context — never fabricate code.
3. Include file paths and line numbers when discussing specific code.
4. For multi-step questions (e.g., "trace the flow from X to Y"), think step by step:
   - Identify the entry point
   - Follow the call chain through the relevant files
   - Explain each step with citations
5. If the context does not contain enough information to answer confidently, say so explicitly. Do not guess.
6. Chunks marked as [CONTEXT] are structural context (e.g., parent class definitions) — use them to understand the code but prefer citing numbered chunks.

Be precise and concise. Explain code clearly."""

    def _build_user_prompt(self, context: str, parent_context: str, question: str) -> str:
        """Build user prompt with context and question."""
        prompt = f"Code chunks from the repository (numbered for citation):\n\n{context}"
        if parent_context:
            prompt += f"\n\n---\n\n[CONTEXT] Structural context (parent classes/modules):\n\n{parent_context}"
        prompt += f"\n\n---\n\nQuestion: {question}\n\nAnswer using citations [1], [2], etc."
        return prompt

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

    async def _rerank_chunks(
        self,
        chunks: list[RetrievedChunk],
        question: str,
    ) -> list[RetrievedChunk]:
        """Rerank retrieved chunks using LLM-based relevance scoring."""
        if not chunks or not self.settings.reranker_enabled:
            return chunks

        try:
            chunk_descriptions = []
            for i, chunk in enumerate(chunks):
                desc = f"[{i}] {chunk.file_path}:{chunk.start_line}-{chunk.end_line}"
                if chunk.symbol_name:
                    desc += f" ({chunk.symbol_name})"
                desc += f"\n{chunk.content[:200]}"
                chunk_descriptions.append(desc)

            response = await self.client.chat.completions.create(
                model=self.settings.reranker_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a code relevance scorer. Given a question and code chunks, "
                            "score each chunk's relevance from 1 (irrelevant) to 5 (highly relevant). "
                            'Respond with ONLY a JSON array: [{"index": 0, "score": 3}, ...]'
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Question: {question}\n\nChunks:\n" + "\n\n".join(chunk_descriptions),
                    },
                ],
                max_completion_tokens=500,
            )

            scores_text = response.choices[0].message.content or "[]"
            scores_text = scores_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            scores = json.loads(scores_text)

            score_map = {item["index"]: item["score"] for item in scores}

            reranked = sorted(
                enumerate(chunks),
                key=lambda x: score_map.get(x[0], 0),
                reverse=True,
            )
            return [chunk for _, chunk in reranked]

        except Exception as e:
            logger.warning("Reranking failed, using original order", error=str(e))
            return chunks

    def _reciprocal_rank_fusion(
        self,
        dense_results: list[dict],
        sparse_results: list[dict],
        k: int = 60,
    ) -> list[dict]:
        """Merge dense and sparse results using Reciprocal Rank Fusion.

        score(doc) = sum(1 / (k + rank)) for each list containing the doc.
        """
        scores: dict[str, float] = {}
        metadata: dict[str, dict] = {}

        for rank, result in enumerate(dense_results):
            doc_id = result["id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
            metadata[doc_id] = result

        for rank, result in enumerate(sparse_results):
            doc_id = result["id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
            if doc_id not in metadata:
                metadata[doc_id] = result

        merged = []
        for doc_id, rrf_score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
            entry = {**metadata[doc_id], "rrf_score": rrf_score}
            merged.append(entry)

        return merged

    async def _fts_search(
        self,
        db,
        version_id,
        keywords: list[str],
        limit: int = 15,
    ) -> list[dict]:
        """Search chunks using Postgres full-text search."""
        if not keywords:
            return []

        from sqlalchemy import text

        result = await db.execute(
            text(
                "SELECT id, ts_rank(fts, plainto_tsquery('english', :query)) as score "
                "FROM chunks "
                "WHERE version_id = :version_id "
                "AND fts @@ plainto_tsquery('english', :query) "
                "ORDER BY score DESC "
                "LIMIT :limit"
            ),
            {"query": " ".join(keywords), "version_id": version_id, "limit": limit},
        )
        rows = result.fetchall()
        return [{"id": str(row.id), "score": float(row.score)} for row in rows]

    async def _log_query(
        self,
        db: AsyncSession,
        repo_id: UUID,
        user_id: UUID,
        question: str,
        metrics: PipelineMetrics,
        model: str,
        answer_length: int,
        citation_count: int,
        prompt_tokens: int,
        completion_tokens: int,
        latency_llm_ms: int | None,
        latency_total_ms: int,
    ) -> None:
        """Persist pipeline quality signals to query_logs (fire-and-forget)."""
        from sqlalchemy import text

        try:
            await db.execute(
                text(
                    "INSERT INTO query_logs ("
                    "  repo_id, user_id, question, effective_question,"
                    "  dense_result_count, sparse_result_count, merged_result_count,"
                    "  post_threshold_count, parent_chunks_added,"
                    "  top_dense_score, top_rrf_score, no_results,"
                    "  model, citation_count, answer_length,"
                    "  prompt_tokens, completion_tokens,"
                    "  hyde_used, reranker_used, history_rewrite_used,"
                    "  latency_history_rewrite_ms, latency_hyde_ms,"
                    "  latency_embedding_ms, latency_dense_search_ms,"
                    "  latency_sparse_search_ms, latency_rerank_ms,"
                    "  latency_llm_ms, latency_total_ms"
                    ") VALUES ("
                    "  :repo_id, :user_id, :question, :effective_question,"
                    "  :dense_result_count, :sparse_result_count, :merged_result_count,"
                    "  :post_threshold_count, :parent_chunks_added,"
                    "  :top_dense_score, :top_rrf_score, :no_results,"
                    "  :model, :citation_count, :answer_length,"
                    "  :prompt_tokens, :completion_tokens,"
                    "  :hyde_used, :reranker_used, :history_rewrite_used,"
                    "  :latency_history_rewrite_ms, :latency_hyde_ms,"
                    "  :latency_embedding_ms, :latency_dense_search_ms,"
                    "  :latency_sparse_search_ms, :latency_rerank_ms,"
                    "  :latency_llm_ms, :latency_total_ms"
                    ")"
                ),
                {
                    "repo_id": repo_id,
                    "user_id": user_id,
                    "question": question,
                    "effective_question": metrics.effective_question,
                    "dense_result_count": metrics.dense_result_count,
                    "sparse_result_count": metrics.sparse_result_count,
                    "merged_result_count": metrics.merged_result_count,
                    "post_threshold_count": metrics.post_threshold_count,
                    "parent_chunks_added": metrics.parent_chunks_added,
                    "top_dense_score": metrics.top_dense_score,
                    "top_rrf_score": metrics.top_rrf_score,
                    "no_results": metrics.no_results,
                    "model": model,
                    "citation_count": citation_count,
                    "answer_length": answer_length,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "hyde_used": metrics.hyde_used,
                    "reranker_used": metrics.reranker_used,
                    "history_rewrite_used": metrics.history_rewrite_used,
                    "latency_history_rewrite_ms": metrics.latency_history_rewrite_ms,
                    "latency_hyde_ms": metrics.latency_hyde_ms,
                    "latency_embedding_ms": metrics.latency_embedding_ms,
                    "latency_dense_search_ms": metrics.latency_dense_search_ms,
                    "latency_sparse_search_ms": metrics.latency_sparse_search_ms,
                    "latency_rerank_ms": metrics.latency_rerank_ms,
                    "latency_llm_ms": latency_llm_ms,
                    "latency_total_ms": latency_total_ms,
                },
            )
        except Exception as e:
            await logger.awarning("Failed to log query metrics", error=str(e))

    async def close(self) -> None:
        """Close the OpenAI client."""
        if self._client is not None:
            await self._client.close()
            self._client = None
