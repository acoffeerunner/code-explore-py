"""Tests for PipelineMetrics dataclass."""

from code_explorer.models.metrics import PipelineMetrics


def test_pipeline_metrics_creation():
    """PipelineMetrics can be created with required fields."""
    metrics = PipelineMetrics(
        dense_result_count=10,
        sparse_result_count=5,
        merged_result_count=12,
        post_threshold_count=8,
        parent_chunks_added=2,
        top_dense_score=0.92,
        top_rrf_score=0.033,
        no_results=False,
        hyde_used=True,
        reranker_used=True,
        history_rewrite_used=False,
        effective_question=None,
        latency_history_rewrite_ms=None,
        latency_hyde_ms=450,
        latency_embedding_ms=120,
        latency_dense_search_ms=80,
        latency_sparse_search_ms=30,
        latency_rerank_ms=1200,
    )
    assert metrics.dense_result_count == 10
    assert metrics.hyde_used is True
    assert metrics.latency_history_rewrite_ms is None


def test_pipeline_metrics_defaults():
    """PipelineMetrics has sensible defaults for optional fields."""
    metrics = PipelineMetrics(
        dense_result_count=0,
        sparse_result_count=0,
        merged_result_count=0,
        post_threshold_count=0,
    )
    assert metrics.no_results is False
    assert metrics.hyde_used is False
    assert metrics.parent_chunks_added == 0
