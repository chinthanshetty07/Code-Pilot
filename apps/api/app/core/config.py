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

    database_url: str = "postgresql+psycopg://codepilot:codepilot@localhost:5433/codepilot"
    redis_url: str = "redis://localhost:6379/0"

    github_client_id: str = ""
    github_client_secret: str = ""
    github_webhook_secret: str = ""

    api_base_url: str = "http://localhost:8010"
    frontend_url: str = "http://localhost:3000"

    # Two LLM providers configured at once rather than one: different agent
    # roles route to whichever is the better capability/throughput fit
    # instead of sharing a single free-tier rate limit. See app/llm/provider.py.
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"

    # Groq by default: gpt-oss-120b would not reliably converge on the
    # search_code/submit_plan loop initially (kept re-searching, never
    # submitting), but that traced back to fixable issues rather than an
    # inherent model limit -- see app/agents/planner.py's forced-retry phase
    # and app/llm/provider.py's GroqLLMProvider for what that took: a real
    # bug (passing tool_choice=None instead of omitting it, which Groq's
    # client treats differently and the API flatly rejects), a schema
    # narrowed to just submit_plan on forced attempts (a fixated model can
    # still attempt a tool that isn't even declared), a plain-language
    # reinforcement alongside the API-level tool_choice constraint, and
    # retrying a forced attempt a few times since even a forced, narrowed
    # turn doesn't always land immediately. With all of that, real runs
    # against a real indexed repo converged 6/7 times. Gemini remains fully
    # configured as an alternative (its main tradeoff being a tight
    # free-tier quota: 5 generate_content requests/minute for
    # gemini-3.6-flash, easy to exhaust across a multi-turn loop).
    planner_llm_provider: str = "groq"

    embedding_provider: str = "gemini"
    embedding_api_key: str = ""
    embedding_model: str = "gemini-embedding-001"

    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
