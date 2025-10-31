"""Tests for hybrid search (RRF fusion) and metadata filtering."""

from uuid import uuid4
from code_explorer.services.chat_service import ChatService


class TestRRFFusion:
    def test_rrf_merges_two_result_lists(self):
        """RRF produces correct merged scores from two ranked lists."""
        id_a, id_b, id_c, id_d = uuid4(), uuid4(), uuid4(), uuid4()

        dense_results = [
            {"id": str(id_a), "score": 0.9},
            {"id": str(id_b), "score": 0.8},
            {"id": str(id_c), "score": 0.7},
        ]
        sparse_results = [
            {"id": str(id_b), "score": 0.95},
            {"id": str(id_d), "score": 0.85},
            {"id": str(id_a), "score": 0.75},
        ]

        service = ChatService.__new__(ChatService)
        merged = service._reciprocal_rank_fusion(dense_results, sparse_results, k=60)

        # id_a and id_b appear in both lists — should have highest RRF scores
        merged_ids = [m["id"] for m in merged]
        assert str(id_a) in merged_ids
        assert str(id_b) in merged_ids
        # Items in both lists should rank higher than items in only one
        both_ids = {str(id_a), str(id_b)}
        single_ids = {str(id_c), str(id_d)}
        both_scores = [m["rrf_score"] for m in merged if m["id"] in both_ids]
        single_scores = [m["rrf_score"] for m in merged if m["id"] in single_ids]
        assert min(both_scores) > max(single_scores)

    def test_rrf_with_empty_sparse(self):
        """RRF works when sparse list is empty (dense-only fallback)."""
        id_a = uuid4()
        dense_results = [{"id": str(id_a), "score": 0.9}]
        service = ChatService.__new__(ChatService)
        merged = service._reciprocal_rank_fusion(dense_results, [], k=60)
        assert len(merged) == 1
        assert merged[0]["id"] == str(id_a)
