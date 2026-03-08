"""Pipeline metrics for quality measurement and observability."""

from dataclasses import dataclass


@dataclass
class PipelineMetrics:
    """Implicit quality signals collected during the RAG query pipeline."""

    # Retrieval counts
    dense_result_count: int = 0
    sparse_result_count: int = 0
    merged_result_count: int = 0
    post_threshold_count: int = 0
    parent_chunks_added: int = 0

    # Score signals
    top_dense_score: float | None = None
    top_rrf_score: float | None = None

    # Outcome flags
    no_results: bool = False

    # Pipeline configuration flags
    hyde_used: bool = False
    reranker_used: bool = False
    history_rewrite_used: bool = False

    # Rewritten question (None if no rewrite)
    effective_question: str | None = None

    # Latency breakdown (milliseconds, None if stage was skipped)
    latency_history_rewrite_ms: int | None = None
    latency_hyde_ms: int | None = None
    latency_embedding_ms: int | None = None
    latency_dense_search_ms: int | None = None
    latency_sparse_search_ms: int | None = None
    latency_rerank_ms: int | None = None
