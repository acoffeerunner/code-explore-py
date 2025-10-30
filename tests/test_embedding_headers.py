"""Tests for structural header prepending before embedding."""

from unittest.mock import patch, MagicMock

from code_explorer.services.embedding_service import EmbeddingService


def _make_service():
    """Create EmbeddingService without requiring real settings."""
    with patch("code_explorer.services.embedding_service.get_settings") as mock_gs:
        mock_gs.return_value = MagicMock()
        return EmbeddingService()


class TestStructuralHeaders:
    def test_build_embed_text_with_all_fields(self):
        """Build enriched text with file path, symbol, language, and docstring."""
        service = _make_service()
        result = service.build_embed_text(
            content="def foo():\n    pass",
            file_path="src/services/auth.py",
            symbol_type="function",
            symbol_name="foo",
            language="python",
            docstring="Authenticate the user.",
        )
        assert result.startswith("# Authenticate the user.\n# Authenticate the user.\n")
        assert "# File: src/services/auth.py | function: foo | Language: python" in result
        assert "def foo():\n    pass" in result

    def test_build_embed_text_without_docstring(self):
        """Build enriched text without docstring."""
        service = _make_service()
        result = service.build_embed_text(
            content="x = 1",
            file_path="config.py",
            symbol_type=None,
            symbol_name=None,
            language="python",
            docstring=None,
        )
        assert result.startswith("# File: config.py")
        assert "x = 1" in result
        # No docstring lines
        assert result.count("# File:") == 1

    def test_build_embed_text_without_symbol(self):
        """Build enriched text for a chunk with no symbol info."""
        service = _make_service()
        result = service.build_embed_text(
            content="import os",
            file_path="utils.py",
            symbol_type=None,
            symbol_name=None,
            language="python",
            docstring=None,
        )
        assert "# File: utils.py | Language: python" in result
        assert "import os" in result
