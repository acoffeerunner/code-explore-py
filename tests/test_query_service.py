"""Tests for query translation services (keyword extraction, metadata parsing)."""

from code_explorer.services.query_service import QueryService


class TestKeywordExtraction:
    def setup_method(self):
        self.service = QueryService()

    def test_extract_camel_case(self):
        keywords = self.service.extract_keywords("how does IndexingService work?")
        assert "IndexingService" in keywords

    def test_extract_snake_case(self):
        keywords = self.service.extract_keywords("what does chunk_file do?")
        assert "chunk_file" in keywords

    def test_extract_quoted_terms(self):
        keywords = self.service.extract_keywords('where is "verify_jwt" defined?')
        assert "verify_jwt" in keywords

    def test_extract_dotted_path(self):
        keywords = self.service.extract_keywords("explain services.chat_service")
        assert "services.chat_service" in keywords

    def test_no_keywords_in_plain_question(self):
        keywords = self.service.extract_keywords("how does authentication work?")
        assert len(keywords) == 0


class TestMetadataFilterExtraction:
    def setup_method(self):
        self.service = QueryService()

    def test_extract_language_filter(self):
        filters = self.service.extract_metadata_filters("how does the Python auth work?")
        assert filters.get("language") == "python"

    def test_extract_no_filters(self):
        filters = self.service.extract_metadata_filters("how does auth work?")
        assert filters == {}

    def test_extract_symbol_type_filter(self):
        filters = self.service.extract_metadata_filters("show me the class for indexing")
        assert filters.get("symbol_type") == "class"

    def test_extract_file_path_hint(self):
        filters = self.service.extract_metadata_filters("in the auth middleware, how does rate limiting work?")
        assert "file_path_hint" in filters
        assert "auth" in filters["file_path_hint"]
