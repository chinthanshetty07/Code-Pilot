from app.core.config import Settings


def test_database_url_normalizes_a_bare_postgresql_scheme() -> None:
    """Regression test for a real deploy-breaking bug found while building
    Milestone 12's Render config: a managed Postgres provider's own
    connection string (e.g. Render's `fromDatabase` property) is a bare
    `postgresql://` URL, and `create_async_engine` on one of those fails
    immediately with `ModuleNotFoundError: No module named 'psycopg2'` --
    confirmed by actually constructing an engine with one, not assumed."""
    settings = Settings(database_url="postgresql://user:pass@host:5432/db")
    assert settings.database_url == "postgresql+psycopg://user:pass@host:5432/db"


def test_database_url_normalizes_the_short_postgres_scheme() -> None:
    # Some providers (historically Heroku, and others that copied its
    # convention) use `postgres://` rather than `postgresql://`.
    settings = Settings(database_url="postgres://user:pass@host:5432/db")
    assert settings.database_url == "postgresql+psycopg://user:pass@host:5432/db"


def test_database_url_leaves_an_already_correct_url_unchanged() -> None:
    settings = Settings(database_url="postgresql+psycopg://user:pass@host:5432/db")
    assert settings.database_url == "postgresql+psycopg://user:pass@host:5432/db"
