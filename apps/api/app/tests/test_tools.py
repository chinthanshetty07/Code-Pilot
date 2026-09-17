import json
import uuid

from app.agents.tools import MAX_SEARCH_RESULT_CONTENT_CHARS, format_search_results
from app.services.search import SearchResult


def _make_result(content: str, **overrides: object) -> SearchResult:
    defaults: dict = dict(
        chunk_id=uuid.uuid4(),
        file_path="src/app.py",
        language="python",
        chunk_type="function",
        symbol_name="greet",
        start_line=1,
        end_line=10,
        content=content,
        score=0.9,
    )
    defaults.update(overrides)
    return SearchResult(**defaults)


def test_format_search_results_leaves_small_content_untouched() -> None:
    result = _make_result("def greet():\n    return 'hi'\n")

    parsed = json.loads(format_search_results([result]))

    assert parsed[0]["content"] == result.content
    assert "truncated" not in parsed[0]["content"]


def test_format_search_results_truncates_oversized_content() -> None:
    # Regression test: a single search_code call could return up to
    # SEARCH_RESULT_LIMIT chunks with no cap on each one's own size --
    # confirmed in production to blow through Groq's 8000 TPM limit after
    # just one or two calls (see MAX_SEARCH_RESULT_CONTENT_CHARS's own
    # comment for the real 413 this caused).
    huge_content = "x" * (MAX_SEARCH_RESULT_CONTENT_CHARS + 5000)
    result = _make_result(huge_content)

    parsed = json.loads(format_search_results([result]))

    assert len(parsed[0]["content"]) < len(huge_content)
    assert parsed[0]["content"].startswith("x" * MAX_SEARCH_RESULT_CONTENT_CHARS)
    assert "truncated" in parsed[0]["content"]
    assert "5000" in parsed[0]["content"]


def test_format_search_results_truncation_bounds_worst_case_total_size() -> None:
    # Ten results, each individually oversized -- proves the cap applies
    # per-result (not just to the first one), which is what actually keeps
    # a real search_code call's total size bounded.
    results = [_make_result("y" * 10_000, file_path=f"src/file_{i}.py") for i in range(10)]

    formatted = format_search_results(results)
    parsed = json.loads(formatted)

    assert len(parsed) == 10
    for entry in parsed:
        # Truncated content plus the appended notice stays well under what
        # the raw 10,000-char content would have contributed.
        assert len(entry["content"]) < MAX_SEARCH_RESULT_CONTENT_CHARS + 200

    assert len(formatted) < 10 * 10_000


def test_format_search_results_preserves_all_other_fields() -> None:
    result = _make_result(
        "short content",
        file_path="src/thing.ts",
        chunk_type="class",
        symbol_name="Thing",
        start_line=5,
        end_line=42,
        score=0.87654,
    )

    parsed = json.loads(format_search_results([result]))[0]

    assert parsed["file_path"] == "src/thing.ts"
    assert parsed["chunk_type"] == "class"
    assert parsed["symbol_name"] == "Thing"
    assert parsed["start_line"] == 5
    assert parsed["end_line"] == 42
    assert parsed["score"] == 0.877  # rounded to 3 places, matching format_search_results


def test_format_search_results_handles_empty_list() -> None:
    assert json.loads(format_search_results([])) == []
