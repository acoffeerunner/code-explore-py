"""Query translation service for keyword extraction and metadata filter parsing."""

from __future__ import annotations

import re

import structlog
from openai import AsyncOpenAI

from code_explorer.config import Settings, get_settings
from code_explorer.utils.langsmith_utils import create_openai_client

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

    def __init__(self, settings: Settings | None = None) -> None:
        if settings is not None:
            self.settings = settings
        else:
            try:
                self.settings = get_settings()
            except Exception:
                self.settings = None  # type: ignore[assignment]
        self._client: AsyncOpenAI | None = None

    def _get_openai_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = create_openai_client(
                self.settings.openai_api_key.get_secret_value(), self.settings,
            )
        return self._client

    async def generate_hyde(self, question: str) -> str:
        """Generate a hypothetical code snippet that answers the question (HyDE).

        Falls back to the original question if the LLM call fails.
        """
        try:
            client = self._get_openai_client()
            response = await client.chat.completions.create(
                model=self.settings.hyde_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Write a short code snippet that would answer this question about a codebase. "
                            "Do not explain, just write plausible code. Include function/class names."
                        ),
                    },
                    {"role": "user", "content": question},
                ],
                max_completion_tokens=300,
            )
            result = response.choices[0].message.content
            if result:
                return result.strip()
            return question
        except Exception as e:
            logger.warning("HyDE generation failed, using original question", error=str(e))
            return question

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

    async def rewrite_with_history(
        self,
        question: str,
        history: list[dict[str, str]],
    ) -> str:
        """Rewrite a follow-up question as a standalone query using chat history.

        If history is empty or the LLM call fails, returns the original question.
        """
        if not history:
            return question

        try:
            client = self._get_openai_client()

            turns = getattr(self.settings, 'chat_history_turns', 5) if self.settings else 5
            model = getattr(self.settings, 'hyde_model', 'gpt-4o-mini') if self.settings else 'gpt-4o-mini'

            # Build a compact history summary
            history_text = "\n".join(
                f"{'User' if h['role'] == 'user' else 'Assistant'}: {h['content'][:200]}"
                for h in history[-turns * 2:]
            )

            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Rewrite the user's follow-up question as a standalone question "
                            "that incorporates context from the conversation history. "
                            "Return ONLY the rewritten question, nothing else."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Conversation history:\n{history_text}\n\nFollow-up question: {question}",
                    },
                ],
                max_completion_tokens=200,
            )
            result = response.choices[0].message.content
            if result:
                return result.strip()
            return question
        except Exception as e:
            logger.warning("History rewrite failed, using original question", error=str(e))
            return question
