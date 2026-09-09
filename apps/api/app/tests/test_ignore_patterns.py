from app.rag.ignore_patterns import should_ignore_path


def test_ignores_files_inside_ignored_directories() -> None:
    assert should_ignore_path("node_modules/some-pkg/index.js") is True
    assert should_ignore_path("apps/web/node_modules/x/y.js") is True
    assert should_ignore_path(".git/HEAD") is True
    assert should_ignore_path("apps/api/__pycache__/main.cpython-312.pyc") is True
    assert should_ignore_path("apps/api/.venv/lib/foo.py") is True


def test_ignores_lockfiles_by_name() -> None:
    assert should_ignore_path("pnpm-lock.yaml") is True
    assert should_ignore_path("apps/api/uv.lock") is True
    assert should_ignore_path("package-lock.json") is True


def test_ignores_binary_extensions() -> None:
    assert should_ignore_path("public/logo.png") is True
    assert should_ignore_path("assets/font.woff2") is True
    assert should_ignore_path("dump.sqlite3") is True


def test_ignores_minified_and_map_files() -> None:
    assert should_ignore_path("dist/bundle.min.js") is True
    assert should_ignore_path("dist/bundle.js.map") is True


def test_allows_real_source_files() -> None:
    assert should_ignore_path("apps/api/app/main.py") is False
    assert should_ignore_path("apps/web/app/page.tsx") is False
    assert should_ignore_path("README.md") is False
    assert should_ignore_path("apps/web/src/components/Button.jsx") is False
