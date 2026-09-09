"""Splits a single file's content into retrieval-sized code chunks.

Pure and synchronous: no DB, no network, no filesystem I/O beyond the
`content` string passed in. Safe to call from anywhere (including a worker
job) and to unit test directly with plain strings.

Design notes:
  - A class chunk always covers the *entire* class body, methods included.
    We deliberately don't also emit separate "method" chunks for methods
    inside a chunked class: retrieval works better when a method's context
    (sibling methods, shared state) stays together, and splitting would
    create near-duplicate/overlapping chunks (the class chunk's text already
    contains every method). `chunk_type="method"` is reserved on CodeChunk
    for a future targeted use case (e.g. re-chunking one oversized class by
    method) but nothing here currently emits it.
  - Only *top-level* constructs are chunked as functions/classes/etc., for
    both Python and JS/TS. Anything else at module scope (imports, constants,
    `if __name__ == "__main__":`, re-exports, ...) is swept up as "leftover"
    and, if substantial, run through the generic line-based text chunker so
    nothing is silently dropped.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_javascript as ts_javascript
import tree_sitter_typescript as ts_typescript
from tree_sitter import Language, Node, Parser


@dataclass
class CodeChunk:
    chunk_type: str  # "function" | "class" | "method" | "interface" | "text"
    symbol_name: str | None
    start_line: int  # 1-indexed, inclusive
    end_line: int  # 1-indexed, inclusive
    content: str


_EXTENSION_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".md": "markdown",
    ".markdown": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".css": "css",
    ".scss": "css",
    ".html": "html",
    ".htm": "html",
    ".txt": "text",
}


def detect_language(file_path: str) -> str:
    """Map a file extension to a short language label. Unknown or missing
    extensions map to 'text'."""
    ext = Path(file_path).suffix.lower()
    return _EXTENSION_LANGUAGE.get(ext, "text")


def chunk_file(file_path: str, content: str) -> list[CodeChunk]:
    """Dispatch to a language-specific chunker based on detect_language(file_path)."""
    if not content or not content.strip():
        return []
    if _is_oversized(content):
        return _chunk_oversized(content)

    language = detect_language(file_path)
    if language == "python":
        return _chunk_python(content)
    if language in ("javascript", "typescript"):
        return _chunk_javascript_like(file_path, content)
    return _chunk_text_lines(content.splitlines())


# ---------------------------------------------------------------------------
# Size guard: don't run ast/tree-sitter over pathological input (a generated
# file that slipped through filtering, a minified bundle, ...). Thresholds
# are deliberately generous for real hand-written source and cheap to check
# up front; content past either one goes straight to fixed-size chunking.
# ---------------------------------------------------------------------------

_MAX_CONTENT_CHARS = 2_000_000  # ~2MB of text
_MAX_SINGLE_LINE_LENGTH = 20_000  # a real source line never gets close to this
_OVERSIZED_CHUNK_CHARS = 8_000
_OVERSIZED_MAX_CHUNKS = 50  # hard cap so a giant file can't blow up chunk count


def _is_oversized(content: str) -> bool:
    if len(content) > _MAX_CONTENT_CHARS:
        return True
    return any(len(line) > _MAX_SINGLE_LINE_LENGTH for line in content.splitlines())


def _chunk_oversized(content: str) -> list[CodeChunk]:
    """Fixed-size character windows, bypassing line-based logic entirely
    (a file that trips the size guard may have unusable "lines")."""
    chunks: list[CodeChunk] = []
    offset = 0
    length = len(content)
    while offset < length and len(chunks) < _OVERSIZED_MAX_CHUNKS:
        end = min(offset + _OVERSIZED_CHUNK_CHARS, length)
        piece = content[offset:end]
        if piece.strip():
            chunks.append(
                CodeChunk(
                    chunk_type="text",
                    symbol_name=None,
                    start_line=content.count("\n", 0, offset) + 1,
                    end_line=content.count("\n", 0, end) + 1,
                    content=piece,
                )
            )
        offset = end
    return chunks


# ---------------------------------------------------------------------------
# Generic line-based text chunker, shared by the plain-text path and by
# "leftover" (non-semantic) segments of Python/JS/TS files.
# ---------------------------------------------------------------------------

_TEXT_CHUNK_LINES = 60
_TEXT_CHUNK_OVERLAP = 10


def _chunk_text_lines(lines: list[str], offset: int = 1) -> list[CodeChunk]:
    """~60-line windows with ~10-line overlap. `offset` is the 1-indexed
    line number of lines[0], so this can chunk a slice of a larger file."""
    n = len(lines)
    if n == 0 or not any(line.strip() for line in lines):
        return []

    if n <= _TEXT_CHUNK_LINES:
        return [
            CodeChunk(
                chunk_type="text",
                symbol_name=None,
                start_line=offset,
                end_line=offset + n - 1,
                content="\n".join(lines),
            )
        ]

    chunks: list[CodeChunk] = []
    step = _TEXT_CHUNK_LINES - _TEXT_CHUNK_OVERLAP
    start = 0
    while True:
        end = min(start + _TEXT_CHUNK_LINES, n)
        window = lines[start:end]
        if any(line.strip() for line in window):
            chunks.append(
                CodeChunk(
                    chunk_type="text",
                    symbol_name=None,
                    start_line=offset + start,
                    end_line=offset + end - 1,
                    content="\n".join(window),
                )
            )
        if end == n:
            break
        start += step
    return chunks


# A leftover gap that's just blank lines, a shebang/comment, or a short run
# of imports isn't worth its own retrieval chunk.
_LEFTOVER_NOISE_RE = re.compile(
    r"^(#!|#\s|//|/\*|\*/|\*\s|"
    r"import\b|from\s+\S+\s+import\b|"
    r"export\s*\{|export\s*\*|"
    r'["\']use (strict|client|server)["\'];?$)'
)


def _is_trivial_leftover(lines: list[str]) -> bool:
    meaningful = [
        line for line in lines if line.strip() and not _LEFTOVER_NOISE_RE.match(line.strip())
    ]
    return len(meaningful) <= 2


def _leftover_chunks(lines: list[str], covered: list[tuple[int, int]]) -> list[CodeChunk]:
    """Text-chunk whatever isn't covered by semantic chunks (1-indexed,
    inclusive `covered` ranges), skipping trivial gaps."""
    total_lines = len(lines)
    chunks: list[CodeChunk] = []
    cursor = 1
    for start, end in sorted(covered):
        if start > cursor:
            gap = lines[cursor - 1 : start - 1]
            if not _is_trivial_leftover(gap):
                chunks.extend(_chunk_text_lines(gap, offset=cursor))
        cursor = max(cursor, end + 1)
    if cursor <= total_lines:
        gap = lines[cursor - 1 : total_lines]
        if not _is_trivial_leftover(gap):
            chunks.extend(_chunk_text_lines(gap, offset=cursor))
    return chunks


# ---------------------------------------------------------------------------
# Python: stdlib `ast`, top-level FunctionDef/AsyncFunctionDef/ClassDef only.
# ---------------------------------------------------------------------------


def _chunk_python(content: str) -> list[CodeChunk]:
    lines = content.splitlines()
    try:
        tree = ast.parse(content)
    except SyntaxError:
        # Not actually valid Python (Py2 script, corrupted file, ...) -- fall
        # back to treating it as plain text rather than raising.
        return _chunk_text_lines(lines)

    total_lines = len(lines)
    body = tree.body
    chunks: list[CodeChunk] = []
    covered: list[tuple[int, int]] = []

    for index, node in enumerate(body):
        if isinstance(node, ast.ClassDef):
            chunk_type = "class"
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            chunk_type = "function"
        else:
            continue

        start = node.lineno
        end = node.end_lineno
        if end is None:
            # Rare on Python 3.8+: fall back to the next top-level sibling's
            # start line, or EOF if this was the last node.
            next_start = body[index + 1].lineno if index + 1 < len(body) else None
            end = (next_start - 1) if next_start else total_lines
        end = min(end, total_lines)

        covered.append((start, end))
        chunks.append(
            CodeChunk(
                chunk_type=chunk_type,
                symbol_name=node.name,
                start_line=start,
                end_line=end,
                content="\n".join(lines[start - 1 : end]),
            )
        )

    chunks.extend(_leftover_chunks(lines, covered))
    chunks.sort(key=lambda c: c.start_line)
    return chunks


# ---------------------------------------------------------------------------
# JavaScript/TypeScript: AST-based via tree-sitter.
#
# We use the `tree-sitter` + `tree-sitter-javascript` + `tree-sitter-typescript`
# packages rather than `tree-sitter-language-pack`: the latter's `get_parser()`
# downloads a compiled grammar `.dylib`/`.so` over the network on first use
# and caches it under the user's home directory (confirmed empirically --
# the cache file mtimes lined up exactly with our first `get_parser()` call,
# and the wheel's RECORD lists no binary). That's a hidden network + external
# filesystem dependency at runtime, which this module must not have. The
# per-language grammar packages instead ship the compiled grammar *inside*
# the wheel (built at publish time), so parsing here is fully offline.
# ---------------------------------------------------------------------------

_TS_FUNCTION_TYPES = {"function_declaration", "generator_function_declaration"}
_TS_CLASS_TYPES = {"class_declaration", "abstract_class_declaration"}
_TS_INTERFACE_TYPES = {"interface_declaration", "type_alias_declaration"}
_TS_DECLARATOR_CONTAINER_TYPES = {"lexical_declaration", "variable_declaration"}
_TS_FUNCTION_VALUE_TYPES = {
    "arrow_function",
    "function_expression",
    "function",
    "generator_function",
}
# A child of export_statement worth unwrapping into (declarations, plus a
# bare `export default () => {}` / `export default function () {}` value).
_TS_EXPORTABLE_TYPES = _TS_FUNCTION_VALUE_TYPES | {
    "lexical_declaration",
    "variable_declaration",
}

_parser_cache: dict[str, Parser] = {}


def _get_parser(file_path: str) -> Parser:
    suffix = Path(file_path).suffix.lower()
    grammar = "tsx" if suffix == ".tsx" else "typescript" if suffix == ".ts" else "javascript"
    parser = _parser_cache.get(grammar)
    if parser is None:
        if grammar == "javascript":
            language = Language(ts_javascript.language())
        elif grammar == "tsx":
            language = Language(ts_typescript.language_tsx())
        else:
            language = Language(ts_typescript.language_typescript())
        parser = Parser(language)
        _parser_cache[grammar] = parser
    return parser


def _unwrap_export(node: Node) -> Node:
    """`export`/`export default` wraps the real declaration one level deep;
    pull it out so callers only match on the inner node's type."""
    if node.type != "export_statement":
        return node
    for child in node.named_children:
        if child.type in _TS_EXPORTABLE_TYPES or child.type.endswith("declaration"):
            return child
    return node


def _node_name(node: Node) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node is None or name_node.text is None:
        return None
    return name_node.text.decode("utf-8", errors="replace")


def _function_valued_declarator_name(container: Node) -> str | None:
    """For `const foo = () => {...}` / `const bar = function () {...}`
    (possibly among other, non-function declarators in the same statement),
    return the name of the first function-valued one."""
    for decl in container.named_children:
        if decl.type != "variable_declarator":
            continue
        value = decl.child_by_field_name("value")
        if value is not None and value.type in _TS_FUNCTION_VALUE_TYPES:
            name = decl.child_by_field_name("name")
            if name is not None and name.text is not None:
                return name.text.decode("utf-8", errors="replace")
    return None


def _chunk_javascript_like(file_path: str, content: str) -> list[CodeChunk]:
    try:
        parser = _get_parser(file_path)
        tree = parser.parse(content.encode("utf-8", errors="replace"))
    except Exception:
        # tree-sitter itself doesn't raise on malformed syntax (it produces
        # ERROR nodes), so this is only a defensive net for the unexpected.
        return _chunk_text_lines(content.splitlines())

    lines = content.splitlines()
    total_lines = len(lines)
    chunks: list[CodeChunk] = []
    covered: list[tuple[int, int]] = []

    for top in tree.root_node.named_children:
        inner = _unwrap_export(top)
        start = top.start_point.row + 1
        end = min(top.end_point.row + 1, total_lines) if total_lines else top.end_point.row + 1

        chunk_type: str | None = None
        symbol_name: str | None = None

        if inner.type in _TS_FUNCTION_TYPES:
            chunk_type = "function"
            symbol_name = _node_name(inner)
        elif inner.type in _TS_CLASS_TYPES:
            chunk_type = "class"
            symbol_name = _node_name(inner)
        elif inner.type in _TS_INTERFACE_TYPES:
            chunk_type = "interface"
            symbol_name = _node_name(inner)
        elif inner.type in _TS_DECLARATOR_CONTAINER_TYPES:
            symbol_name = _function_valued_declarator_name(inner)
            if symbol_name is not None:
                chunk_type = "function"
        elif inner.type in _TS_FUNCTION_VALUE_TYPES:
            # Anonymous `export default () => {...}` / `export default function () {...}`.
            chunk_type = "function"
            symbol_name = None

        if chunk_type is None:
            continue

        covered.append((start, end))
        chunks.append(
            CodeChunk(
                chunk_type=chunk_type,
                symbol_name=symbol_name,
                start_line=start,
                end_line=end,
                content="\n".join(lines[start - 1 : end]),
            )
        )

    chunks.extend(_leftover_chunks(lines, covered))
    chunks.sort(key=lambda c: c.start_line)
    return chunks
