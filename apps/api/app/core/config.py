from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    secret_key: str = "change-me-in-production"

    database_url: str = "postgresql+psycopg://codepilot:codepilot@localhost:5432/codepilot"
    redis_url: str = "redis://localhost:6379/0"

    github_client_id: str = ""
    github_client_secret: str = ""
    github_webhook_secret: str = ""

    llm_provider: str = "anthropic"
    llm_api_key: str = ""
    llm_model: str = "claude-sonnet-5"

    embedding_provider: str = "openai"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"

    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
