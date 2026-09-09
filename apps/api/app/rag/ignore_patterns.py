DEFAULT_IGNORED_DIRS = {
    "node_modules",
    ".git",
    "dist",
    "build",
    "coverage",
    ".next",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "target",
    ".gradle",
    ".idea",
    ".vscode",
    "vendor",
    ".terraform",
    ".turbo",
    "out",
    ".cache",
}

DEFAULT_IGNORED_FILENAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "uv.lock",
    "poetry.lock",
    "Cargo.lock",
    "composer.lock",
    "Gemfile.lock",
}

DEFAULT_IGNORED_FILE_SUFFIXES = (
    ".lock",
    ".min.js",
    ".min.css",
    ".map",
)

BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".webp",
    ".bmp",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".mp3",
    ".wav",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".class",
    ".jar",
    ".pyc",
    ".db",
    ".sqlite",
    ".sqlite3",
}

MAX_FILE_SIZE_BYTES = 500_000


def should_ignore_path(path: str) -> bool:
    parts = path.split("/")
    if any(part in DEFAULT_IGNORED_DIRS for part in parts[:-1]):
        return True

    filename = parts[-1]
    if filename in DEFAULT_IGNORED_FILENAMES:
        return True
    if any(filename.endswith(suffix) for suffix in DEFAULT_IGNORED_FILE_SUFFIXES):
        return True

    ext = f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else ""
    return ext in BINARY_EXTENSIONS
