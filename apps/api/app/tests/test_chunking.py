from app.rag.chunking import CodeChunk, chunk_file, detect_language

PYTHON_SAMPLE = """import os
import sys


def top_function_one(x):
    return x + 1


def top_function_two(x, y):
    return x + y


class Widget:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return f"hello {self.name}"


if __name__ == "__main__":
    print(top_function_one(1))
    print(top_function_two(1, 2))
    print("done")
"""

TS_SAMPLE = """export function add(a: number, b: number): number {
  return a + b;
}

export class Calculator {
  constructor(private base: number) {}

  addToBase(x: number): number {
    return this.base + x;
  }
}

export interface Point {
  x: number;
  y: number;
}

const multiply = (a: number, b: number): number => {
  return a * b;
};
"""


def _sliced(content: str, chunk: CodeChunk) -> str:
    lines = content.splitlines()
    return "\n".join(lines[chunk.start_line - 1 : chunk.end_line])


def test_python_functions_and_class_with_methods() -> None:
    chunks = chunk_file("sample.py", PYTHON_SAMPLE)

    functions = [c for c in chunks if c.chunk_type == "function"]
    classes = [c for c in chunks if c.chunk_type == "class"]
    methods = [c for c in chunks if c.chunk_type == "method"]

    assert [c.symbol_name for c in functions] == ["top_function_one", "top_function_two"]
    assert len(classes) == 1
    # One chunk per class, not one per method.
    assert methods == []

    for func in functions:
        sliced = _sliced(PYTHON_SAMPLE, func)
        assert sliced.startswith(f"def {func.symbol_name}(")
        assert sliced == func.content

    widget = classes[0]
    assert widget.symbol_name == "Widget"
    sliced = _sliced(PYTHON_SAMPLE, widget)
    assert sliced.startswith("class Widget:")
    assert sliced == widget.content
    # The whole class body -- both methods -- is in the one chunk.
    assert "def __init__" in widget.content
    assert "def greet" in widget.content

    # The trailing `if __name__ == "__main__":` block (3+ meaningful lines)
    # is non-trivial leftover and gets its own text chunk.
    leftovers = [c for c in chunks if c.chunk_type == "text"]
    assert any('if __name__ == "__main__"' in c.content for c in leftovers)


def test_python_syntax_error_falls_back_to_text_chunking() -> None:
    broken = "def broken(:\n    pass\n\nclass Also(:\n    pass\n"

    chunks = chunk_file("broken.py", broken)  # must not raise

    assert chunks
    assert all(c.chunk_type == "text" for c in chunks)
    assert "".join(c.content for c in chunks).count("def broken") == 1


def test_python_empty_and_whitespace_only_files_return_no_chunks() -> None:
    assert chunk_file("empty.py", "") == []
    assert chunk_file("blank.py", "   \n\n\t\n  ") == []


def test_typescript_function_class_interface_and_arrow_const() -> None:
    chunks = chunk_file("sample.ts", TS_SAMPLE)
    by_name = {c.symbol_name: c for c in chunks}

    assert by_name["add"].chunk_type == "function"
    assert by_name["Calculator"].chunk_type == "class"
    assert by_name["Point"].chunk_type == "interface"
    # `const multiply = (a, b) => {...}` is chunked as a function too.
    assert by_name["multiply"].chunk_type == "function"

    # The class chunk covers the whole body, including its method.
    assert "addToBase" in by_name["Calculator"].content

    for chunk in (by_name["add"], by_name["Calculator"], by_name["Point"], by_name["multiply"]):
        sliced = _sliced(TS_SAMPLE, chunk)
        assert sliced == chunk.content


def test_javascript_function_and_class() -> None:
    js_sample = """const config = require('./config');

function greet(name) {
  return `hi ${name}`;
}

class Store {
  constructor() {
    this.state = {};
  }

  get(key) {
    return this.state[key];
  }
}

module.exports = { greet, Store };
"""
    chunks = chunk_file("sample.js", js_sample)
    by_name = {c.symbol_name: c for c in chunks}

    assert by_name["greet"].chunk_type == "function"
    assert by_name["Store"].chunk_type == "class"
    assert "get(key)" in by_name["Store"].content
    # No chunk emitted per-method inside a chunked class.
    assert all(c.chunk_type != "method" for c in chunks)


def test_jsx_default_export_function_and_arrow_component() -> None:
    jsx_sample = """import React from 'react';

export default function App() {
  return <div className="app">Hello</div>;
}

export const Widget = () => {
  return <span>Widget</span>;
};
"""
    chunks = chunk_file("sample.jsx", jsx_sample)
    by_name = {c.symbol_name: c for c in chunks}

    assert by_name["App"].chunk_type == "function"
    assert by_name["Widget"].chunk_type == "function"


def test_text_file_longer_than_one_chunk_overlaps_between_consecutive_chunks() -> None:
    lines = [f"Line {i:04d} of the document body text here." for i in range(1, 131)]
    content = "\n".join(lines)

    chunks = chunk_file("notes.md", content)

    assert len(chunks) > 1
    assert all(c.chunk_type == "text" and c.symbol_name is None for c in chunks)

    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        prev_lines = prev.content.splitlines()
        next_lines = nxt.content.splitlines()
        overlap = prev_lines[-10:]
        assert overlap == next_lines[: len(overlap)]
        assert len(overlap) > 0

    # Every line of the original file shows up somewhere.
    covered_text = "\n".join(c.content for c in chunks)
    for line in lines:
        assert line in covered_text


def test_text_file_shorter_than_one_chunk_is_a_single_chunk() -> None:
    content = "\n".join(f"line {i}" for i in range(5))

    chunks = chunk_file("short.md", content)

    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 5
    assert chunks[0].content == content


def test_detect_language_maps_known_and_unknown_extensions() -> None:
    assert detect_language("main.py") == "python"
    assert detect_language("app.js") == "javascript"
    assert detect_language("component.jsx") == "javascript"
    assert detect_language("module.ts") == "typescript"
    assert detect_language("component.tsx") == "typescript"
    assert detect_language("README.md") == "markdown"
    assert detect_language("data.json") == "json"
    assert detect_language("styles.css") == "css"
    assert detect_language("data.some_unknown_ext") == "text"
    assert detect_language("Dockerfile") == "text"
