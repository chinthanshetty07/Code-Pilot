"""Tool definitions shared across agents. search_code is the only one both
the Planner and Coder agents use identically; each agent's own file keeps
whatever tools are specific to it (submit_plan, read_file/edit_file/...)."""

import json

from app.llm.provider import ToolSpec

SEARCH_RESULT_LIMIT = 10

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


def format_search_results(results: list) -> str:
    return json.dumps(
        [
            {
                "file_path": r.file_path,
                "chunk_type": r.chunk_type,
                "symbol_name": r.symbol_name,
                "start_line": r.start_line,
                "end_line": r.end_line,
                "content": r.content,
                "score": round(r.score, 3),
            }
            for r in results
        ]
    )
