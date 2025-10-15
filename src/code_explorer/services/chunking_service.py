"""Tree-sitter based code chunking service for AST-aware code parsing."""

import hashlib
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog
import tiktoken

from code_explorer.models.domain import CodeChunk, Language, SymbolType

logger = structlog.get_logger(__name__)

# Token limits for chunking
MAX_CHUNK_TOKENS = 512
OVERLAP_TOKENS = 128

# Supported file extensions
SUPPORTED_EXTENSIONS: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".go": Language.GO,
    ".java": Language.JAVA,
    ".cs": Language.CSHARP,
    ".md": Language.MARKDOWN,
}


class ChunkingError(Exception):
    """Error during code chunking."""

    pass


class ChunkingService:
    """Service for parsing and chunking source code using tree-sitter."""

    def __init__(self) -> None:
        """Initialize chunking service."""
        self._parsers: dict[Language, Any] = {}
        self._tokenizer = tiktoken.get_encoding("cl100k_base")
        self._tree_sitter_loaded = False

    def _load_tree_sitter(self) -> None:
        """Lazy load tree-sitter parsers."""
        if self._tree_sitter_loaded:
            return

        try:
            import tree_sitter_python
            import tree_sitter_javascript
            import tree_sitter_typescript
            import tree_sitter_go
            import tree_sitter_java
            import tree_sitter_c_sharp
            from tree_sitter import Language as TSLanguage, Parser

            # Initialize parsers for each language
            self._parsers[Language.PYTHON] = self._create_parser(
                TSLanguage(tree_sitter_python.language())
            )
            self._parsers[Language.JAVASCRIPT] = self._create_parser(
                TSLanguage(tree_sitter_javascript.language())
            )
            self._parsers[Language.TYPESCRIPT] = self._create_parser(
                TSLanguage(tree_sitter_typescript.language_typescript())
            )
            # TSX uses typescript parser
            self._parsers[Language.GO] = self._create_parser(
                TSLanguage(tree_sitter_go.language())
            )
            self._parsers[Language.JAVA] = self._create_parser(
                TSLanguage(tree_sitter_java.language())
            )
            self._parsers[Language.CSHARP] = self._create_parser(
                TSLanguage(tree_sitter_c_sharp.language())
            )

            self._tree_sitter_loaded = True
            logger.info("Tree-sitter parsers loaded")

        except ImportError as e:
            logger.warning("Tree-sitter not available, using fallback chunking", error=str(e))

    def _create_parser(self, language: Any) -> Any:
        """Create a tree-sitter parser for a language."""
        from tree_sitter import Parser

        parser = Parser()
        parser.language = language
        return parser

    def count_tokens(self, text: str) -> int:
        """Count tokens in text using tiktoken."""
        return len(self._tokenizer.encode(text))

    def get_content_hash(self, content: str) -> str:
        """Generate SHA256 hash of content."""
        return hashlib.sha256(content.encode()).hexdigest()

    def get_language(self, file_path: Path) -> Language:
        """Determine language from file extension."""
        ext = file_path.suffix.lower()
        return SUPPORTED_EXTENSIONS.get(ext, Language.UNKNOWN)

    def is_supported_file(self, file_path: Path) -> bool:
        """Check if file is supported for chunking."""
        return file_path.suffix.lower() in SUPPORTED_EXTENSIONS

    async def chunk_file(
        self,
        file_path: Path,
        repo_id: UUID,
        version_id: UUID,
        relative_path: str,
    ) -> list[CodeChunk]:
        """
        Parse and chunk a source file.

        Args:
            file_path: Absolute path to the file
            repo_id: Repository UUID
            version_id: Index version UUID
            relative_path: Path relative to repo root

        Returns:
            List of CodeChunk objects
        """
        log = logger.bind(file=relative_path)

        if not file_path.exists():
            await log.awarning("File not found")
            return []

        language = self.get_language(file_path)
        if language == Language.UNKNOWN:
            await log.adebug("Unsupported file type")
            return []

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            await log.awarning("Failed to read file", error=str(e))
            return []

        if not content.strip():
            return []

        # Try tree-sitter parsing first
        self._load_tree_sitter()
        if language in self._parsers:
            try:
                chunks = await self._chunk_with_tree_sitter(
                    content=content,
                    language=language,
                    repo_id=repo_id,
                    version_id=version_id,
                    relative_path=relative_path,
                )
                await log.ainfo("File chunked with tree-sitter", chunk_count=len(chunks))
                return chunks
            except Exception as e:
                await log.awarning("Tree-sitter parsing failed, using fallback", error=str(e))

        # Fallback to simple chunking
        chunks = await self._chunk_simple(
            content=content,
            language=language,
            repo_id=repo_id,
            version_id=version_id,
            relative_path=relative_path,
        )
        await log.ainfo("File chunked with fallback", chunk_count=len(chunks))
        return chunks

    async def _chunk_with_tree_sitter(
        self,
        content: str,
        language: Language,
        repo_id: UUID,
        version_id: UUID,
        relative_path: str,
    ) -> list[CodeChunk]:
        """Chunk code using tree-sitter AST parsing."""
        parser = self._parsers[language]
        tree = parser.parse(bytes(content, "utf-8"))
        root = tree.root_node

        chunks: list[CodeChunk] = []
        lines = content.split("\n")

        # Define node types to extract for each language
        symbol_node_types = self._get_symbol_node_types(language)

        # Walk the AST and extract symbol definitions
        symbols = self._extract_symbols(root, symbol_node_types, lines)

        for symbol in symbols:
            symbol_content = "\n".join(lines[symbol["start_line"] : symbol["end_line"] + 1])
            token_count = self.count_tokens(symbol_content)

            if token_count <= MAX_CHUNK_TOKENS:
                # Single chunk for small symbols
                chunks.append(
                    CodeChunk(
                        id=uuid4(),
                        version_id=version_id,
                        repo_id=repo_id,
                        file_path=relative_path,
                        start_line=symbol["start_line"] + 1,  # 1-indexed
                        end_line=symbol["end_line"] + 1,
                        symbol_name=symbol["name"],
                        symbol_type=symbol["type"],
                        language=language,
                        content=symbol_content,
                        token_count=token_count,
                        content_hash=self.get_content_hash(symbol_content),
                    )
                )
            else:
                # Split large symbols with sliding window
                sub_chunks = self._sliding_window_chunk(
                    lines=lines[symbol["start_line"] : symbol["end_line"] + 1],
                    base_line=symbol["start_line"],
                    symbol_name=symbol["name"],
                    symbol_type=symbol["type"],
                    language=language,
                    repo_id=repo_id,
                    version_id=version_id,
                    relative_path=relative_path,
                )
                chunks.extend(sub_chunks)

        return chunks

    def _get_symbol_node_types(self, language: Language) -> dict[str, SymbolType]:
        """Get AST node types that represent symbols for a language."""
        common = {
            "function_definition": SymbolType.FUNCTION,
            "class_definition": SymbolType.CLASS,
            "method_definition": SymbolType.METHOD,
        }

        language_specific: dict[Language, dict[str, SymbolType]] = {
            Language.PYTHON: {
                "function_definition": SymbolType.FUNCTION,
                "class_definition": SymbolType.CLASS,
                "async_function_definition": SymbolType.FUNCTION,
            },
            Language.JAVASCRIPT: {
                "function_declaration": SymbolType.FUNCTION,
                "class_declaration": SymbolType.CLASS,
                "method_definition": SymbolType.METHOD,
                "arrow_function": SymbolType.FUNCTION,
                "function": SymbolType.FUNCTION,
            },
            Language.TYPESCRIPT: {
                "function_declaration": SymbolType.FUNCTION,
                "class_declaration": SymbolType.CLASS,
                "method_definition": SymbolType.METHOD,
                "interface_declaration": SymbolType.INTERFACE,
                "type_alias_declaration": SymbolType.INTERFACE,
                "arrow_function": SymbolType.FUNCTION,
            },
            Language.GO: {
                "function_declaration": SymbolType.FUNCTION,
                "method_declaration": SymbolType.METHOD,
                "type_declaration": SymbolType.CLASS,
            },
            Language.JAVA: {
                "method_declaration": SymbolType.METHOD,
                "class_declaration": SymbolType.CLASS,
                "interface_declaration": SymbolType.INTERFACE,
                "constructor_declaration": SymbolType.METHOD,
            },
            Language.CSHARP: {
                "method_declaration": SymbolType.METHOD,
                "class_declaration": SymbolType.CLASS,
                "interface_declaration": SymbolType.INTERFACE,
                "constructor_declaration": SymbolType.METHOD,
            },
        }

        return language_specific.get(language, common)

    def _extract_symbols(
        self,
        node: Any,
        symbol_types: dict[str, SymbolType],
        lines: list[str],
    ) -> list[dict]:
        """Recursively extract symbol definitions from AST."""
        symbols: list[dict] = []

        if node.type in symbol_types:
            # Extract symbol name
            name = self._extract_symbol_name(node)
            symbols.append(
                {
                    "name": name,
                    "type": symbol_types[node.type],
                    "start_line": node.start_point[0],
                    "end_line": node.end_point[0],
                }
            )
        else:
            # Recurse into children
            for child in node.children:
                symbols.extend(self._extract_symbols(child, symbol_types, lines))

        return symbols

    def _extract_symbol_name(self, node: Any) -> str | None:
        """Extract the name of a symbol from its AST node."""
        # Look for identifier/name child nodes
        for child in node.children:
            if child.type in ("identifier", "name", "property_identifier"):
                return child.text.decode("utf-8")
            if child.type == "type_identifier":
                return child.text.decode("utf-8")

        return None

    def _sliding_window_chunk(
        self,
        lines: list[str],
        base_line: int,
        symbol_name: str | None,
        symbol_type: SymbolType | None,
        language: Language,
        repo_id: UUID,
        version_id: UUID,
        relative_path: str,
    ) -> list[CodeChunk]:
        """Split large content into overlapping chunks."""
        chunks: list[CodeChunk] = []
        current_lines: list[str] = []
        current_start = 0
        current_tokens = 0

        for i, line in enumerate(lines):
            line_tokens = self.count_tokens(line + "\n")

            if current_tokens + line_tokens > MAX_CHUNK_TOKENS and current_lines:
                # Emit current chunk
                chunk_content = "\n".join(current_lines)
                chunks.append(
                    CodeChunk(
                        id=uuid4(),
                        version_id=version_id,
                        repo_id=repo_id,
                        file_path=relative_path,
                        start_line=base_line + current_start + 1,
                        end_line=base_line + current_start + len(current_lines),
                        symbol_name=symbol_name,
                        symbol_type=symbol_type,
                        language=language,
                        content=chunk_content,
                        token_count=self.count_tokens(chunk_content),
                        content_hash=self.get_content_hash(chunk_content),
                    )
                )

                # Calculate overlap
                overlap_lines: list[str] = []
                overlap_tokens = 0
                for j in range(len(current_lines) - 1, -1, -1):
                    line_tok = self.count_tokens(current_lines[j] + "\n")
                    if overlap_tokens + line_tok > OVERLAP_TOKENS:
                        break
                    overlap_lines.insert(0, current_lines[j])
                    overlap_tokens += line_tok

                current_start = current_start + len(current_lines) - len(overlap_lines)
                current_lines = overlap_lines.copy()
                current_tokens = overlap_tokens

            current_lines.append(line)
            current_tokens += line_tokens

        # Emit final chunk
        if current_lines:
            chunk_content = "\n".join(current_lines)
            chunks.append(
                CodeChunk(
                    id=uuid4(),
                    version_id=version_id,
                    repo_id=repo_id,
                    file_path=relative_path,
                    start_line=base_line + current_start + 1,
                    end_line=base_line + current_start + len(current_lines),
                    symbol_name=symbol_name,
                    symbol_type=symbol_type,
                    language=language,
                    content=chunk_content,
                    token_count=self.count_tokens(chunk_content),
                    content_hash=self.get_content_hash(chunk_content),
                )
            )

        return chunks

    async def _chunk_simple(
        self,
        content: str,
        language: Language,
        repo_id: UUID,
        version_id: UUID,
        relative_path: str,
    ) -> list[CodeChunk]:
        """Fallback chunking without AST parsing (sliding window only)."""
        lines = content.split("\n")
        chunks = self._sliding_window_chunk(
            lines=lines,
            base_line=0,
            symbol_name=None,
            symbol_type=None,
            language=language,
            repo_id=repo_id,
            version_id=version_id,
            relative_path=relative_path,
        )
        return chunks

    async def scan_directory(self, repo_path: Path) -> list[Path]:
        """
        Scan directory for supported source files.

        Args:
            repo_path: Path to repository root

        Returns:
            List of paths to supported source files
        """
        files: list[Path] = []

        # Skip these directories
        skip_dirs = {
            ".git",
            "node_modules",
            "__pycache__",
            ".venv",
            "venv",
            "vendor",
            "dist",
            "build",
            ".tox",
            ".mypy_cache",
            ".pytest_cache",
        }

        for path in repo_path.rglob("*"):
            if path.is_file() and self.is_supported_file(path):
                # Check if in skipped directory
                if not any(skip in path.parts for skip in skip_dirs):
                    files.append(path)

        logger.info("Scanned directory", file_count=len(files))
        return files
