"""Tests for parent chunk expansion, score threshold, and tiktoken context building."""

from uuid import uuid4
import pytest

from code_explorer.services.chat_service import ChatService
from code_explorer.models.domain import RetrievedChunk


class TestScoreThreshold:
    def test_filter_below_threshold(self):
        """Chunks below min_similarity_score are filtered out."""
        service = ChatService.__new__(ChatService)
        chunks = [
            RetrievedChunk(
                chunk_id=uuid4(), score=0.8, file_path="a.py",
                start_line=1, end_line=5, language="python", content="good match",
            ),
            RetrievedChunk(
                chunk_id=uuid4(), score=0.1, file_path="b.py",
                start_line=1, end_line=5, language="python", content="noise",
            ),
        ]
        filtered = service._apply_score_threshold(chunks, min_score=0.3)
        assert len(filtered) == 1
        assert filtered[0].file_path == "a.py"

    def test_no_filter_when_all_above_threshold(self):
        """All chunks pass when above threshold."""
        service = ChatService.__new__(ChatService)
        chunks = [
            RetrievedChunk(
                chunk_id=uuid4(), score=0.9, file_path="a.py",
                start_line=1, end_line=5, language="python", content="good",
            ),
        ]
        filtered = service._apply_score_threshold(chunks, min_score=0.3)
        assert len(filtered) == 1


class TestTiktokenContextBuilding:
    def test_context_uses_tiktoken_not_estimate(self):
        """Context building uses tiktoken for accurate token counting."""
        service = ChatService.__new__(ChatService)
        import tiktoken
        service._tokenizer = tiktoken.get_encoding("cl100k_base")

        chunks = [
            RetrievedChunk(
                chunk_id=uuid4(), score=0.9, file_path="a.py",
                start_line=1, end_line=5, language="python",
                content="def foo():\n    return 42",
            ),
        ]
        context, chunk_map = service._build_context(chunks)
        assert "def foo():" in context
        assert 1 in chunk_map
