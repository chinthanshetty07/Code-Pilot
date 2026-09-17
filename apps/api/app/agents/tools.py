"""Tool definitions shared across agents -- search_code (Planner, Coder,
Reviewer) and read_file (Coder, Reviewer) are identical everywhere they're
used, so they live here once. Each agent's own file keeps whatever tools
are specific to it (submit_plan, create_file/edit_file/get_git_diff,
submit_review, ...); a shared tool's description stays agent-neutral --
guidance specific to one agent's use of it (e.g. Coder's "read a file
before editing it") belongs in that agent's own system prompt, not here.
"""

import json

from app.llm.provider import ToolSpec

# Both of these were tightened together after a live verification against a
# real, previously-failing production issue: an initial pass at
# SEARCH_RESULT_LIMIT=10 / MAX_SEARCH_RESULT_CONTENT_CHARS=1200 cut the
# worst observed request from 12229 to 8305 tokens -- real progress, but
# still over Groq's 8000 TPM limit on the free/on_demand tier by 305 tokens,
# re-triggering the identical 413 "Request too large" this whole mechanism
# exists to prevent. These values replaced that pass only after re-running
# the exact same real issue and confirming it actually succeeds -- see
# planning_error on affected issues for the production failures this fixes.
SEARCH_RESULT_LIMIT = 6

# A single search_code call returns up to SEARCH_RESULT_LIMIT chunks with no
# cap on each one's own size -- fine for a small demo repo, but a real
# hard-failure mode for a real codebase: confirmed live against a genuinely
# indexed repository where the 10 highest-scoring chunks alone summed to
# over 20,000 characters (~5000+ tokens) of raw content, before any JSON
# structure overhead, system prompt, or earlier turns in the same
# conversation. 800 chars trims more than just the largest outlier chunks
# (the real repository's median/mean was ~900 chars) -- a deliberate
# trade-off of a bit of per-chunk context for actually fitting a real,
# tight token budget, made only after confirming empirically that the
# previous, more generous threshold still wasn't enough.
MAX_SEARCH_RESULT_CONTENT_CHARS = 800

SEARCH_CODE_TOOL = ToolSpec(
    name="search_code",
    description=(
        "Semantic search over this repository's indexed code. Returns the most relevant "
        "code chunks (file path, symbol, line range, content) for a natural-language or "
        "code-like query. Call this multiple times with different queries to build a "
        "complete picture -- e.g. the reported symptom, the likely component, related tests."
    ),
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to search for"}},
        "required": ["query"],
    },
)

READ_FILE_TOOL = ToolSpec(
    name="read_file",
    description=(
        "Read a file's current, real content from the repository, by path relative to the "
        "repository root."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the repository root"}
        },
        "required": ["path"],
    },
)


def _truncate_chunk_content(content: str) -> str:
    # Deliberately agent-neutral (no "use read_file instead" pointer): this
    # function is shared by the Planner, which has no read_file tool at all
    # (see this module's own docstring on why shared-tool text stays
    # agent-neutral).
    if len(content) <= MAX_SEARCH_RESULT_CONTENT_CHARS:
        return content
    remaining = len(content) - MAX_SEARCH_RESULT_CONTENT_CHARS
    return (
        content[:MAX_SEARCH_RESULT_CONTENT_CHARS]
        + f"\n\n[... truncated, {remaining} more characters. This is only part of "
        f"{content.count(chr(10)) + 1} total lines in this chunk.]"
    )


def format_search_results(results: list) -> str:
    return json.dumps(
        [
            {
                "file_path": r.file_path,
                "chunk_type": r.chunk_type,
                "symbol_name": r.symbol_name,
                "start_line": r.start_line,
                "end_line": r.end_line,
                "content": _truncate_chunk_content(r.content),
                "score": round(r.score, 3),
            }
            for r in results
        ]
    )
