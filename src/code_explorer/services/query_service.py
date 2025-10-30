"""Query translation service for keyword extraction and metadata filter parsing."""

import re

import structlog

logger = structlog.get_logger(__name__)

# Language name -> enum value mapping
LANGUAGE_NAMES: dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "js": "javascript",
    "typescript": "typescript",
    "ts": "typescript",
    "go": "go",
    "golang": "go",
    "java": "java",
    "c#": "csharp",
    "csharp": "csharp",
}

# Symbol type keywords
SYMBOL_TYPE_KEYWORDS: dict[str, str] = {
    "class": "class",
    "classes": "class",
    "function": "function",
    "functions": "function",
    "method": "method",
    "methods": "method",
    "interface": "interface",
    "interfaces": "interface",
}

# Common path-like keywords that hint at file locations
PATH_HINT_PATTERNS = [
    r"\bin\s+(?:the\s+)?(\w+(?:\s+\w+)?)\s+(?:middleware|service|route|module|component|handler|controller)",
    r"\bin\s+(\w+/\w+)",
    r"(?:middleware|service|route|module)(?:\s+called)?\s+(\w+)",
]


class QueryService:
    """Service for query translation: keyword extraction and metadata filter parsing."""

    def extract_keywords(self, question: str) -> list[str]:
        """Extract likely code identifiers from a natural language question.

        Matches: CamelCase, snake_case, dotted.paths, and "quoted terms".
        """
        keywords: list[str] = []

        # Quoted terms
        for match in re.findall(r'"([^"]+)"', question):
            keywords.append(match)

        # Remove quoted terms from question for further extraction
        cleaned = re.sub(r'"[^"]*"', '', question)

        # CamelCase identifiers (at least two uppercase letters, e.g., IndexingService)
        for match in re.findall(r'\b([A-Z][a-z]+(?:[A-Z][a-z]*)+)\b', cleaned):
            if match not in keywords:
                keywords.append(match)

        # snake_case identifiers (word_word pattern)
        for match in re.findall(r'\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b', cleaned):
            if match not in keywords:
                keywords.append(match)

        # Dotted paths (e.g., services.chat_service)
        for match in re.findall(r'\b([a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)+)\b', cleaned):
            if match not in keywords:
                keywords.append(match)

        return keywords

    def extract_metadata_filters(self, question: str) -> dict[str, str]:
        """Extract Pinecone metadata filters from a natural language question.

        Extracts:
        - language: programming language mentions
        - symbol_type: class/function/method mentions
        - file_path_hint: path-like references for file_path prefix filtering
        """
        filters: dict[str, str] = {}
        lower = question.lower()

        # Language detection
        for lang_name, lang_value in LANGUAGE_NAMES.items():
            # Word boundary match to avoid false positives
            if re.search(rf'\b{re.escape(lang_name)}\b', lower):
                filters["language"] = lang_value
                break

        # Symbol type detection
        for keyword, symbol_type in SYMBOL_TYPE_KEYWORDS.items():
            if re.search(rf'\bthe\s+{keyword}\b|\b{keyword}\s+(?:for|of|that|called)\b', lower):
                filters["symbol_type"] = symbol_type
                break

        # File path hints
        for pattern in PATH_HINT_PATTERNS:
            match = re.search(pattern, lower)
            if match:
                filters["file_path_hint"] = match.group(1).strip()
                break

        return filters
