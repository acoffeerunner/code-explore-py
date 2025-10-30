"""Tests for docstring extraction during tree-sitter chunking."""

import pytest
from code_explorer.services.chunking_service import ChunkingService


class TestDocstringExtraction:
    def setup_method(self):
        self.service = ChunkingService()

    def test_extract_python_docstring(self):
        """Extract docstring from a Python function node."""
        code = '''def greet(name: str) -> str:
    """Return a greeting for the given name."""
    return f"Hello, {name}!"
'''
        # _load_tree_sitter must be called first
        self.service._load_tree_sitter()
        from code_explorer.models.domain import Language
        parser = self.service._parsers.get(Language.PYTHON)
        if parser is None:
            pytest.skip("tree-sitter-python not available")

        tree = parser.parse(bytes(code, "utf-8"))
        # The root's first child should be the function_definition
        func_node = tree.root_node.children[0]
        docstring = self.service._extract_docstring(func_node, code.split("\n"))
        assert docstring == "Return a greeting for the given name."

    def test_extract_no_docstring(self):
        """Return None when no docstring is present."""
        code = '''def greet(name: str) -> str:
    return f"Hello, {name}!"
'''
        self.service._load_tree_sitter()
        from code_explorer.models.domain import Language
        parser = self.service._parsers.get(Language.PYTHON)
        if parser is None:
            pytest.skip("tree-sitter-python not available")

        tree = parser.parse(bytes(code, "utf-8"))
        func_node = tree.root_node.children[0]
        docstring = self.service._extract_docstring(func_node, code.split("\n"))
        assert docstring is None
