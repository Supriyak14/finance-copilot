"""App settings, read from the .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AI Personal Finance Copilot"
    debug: bool = True
    database_url: str = "sqlite:///./data/finance.db"
    anthropic_api_key: str = ""
    # auto  — use the real API, drop to built-in demo replies if it fails
    # always— never call the API (a demo that cannot fail on stage)
    # off   — no fallback; surface the real error
    ai_demo_mode: str = "auto"
    # A deployed container starts with an empty database, which makes for a
    # dull demo. On means: build the sample history at startup, but only if
    # nothing has been logged yet, so a real database is never touched.
    seed_demo_data: bool = False


settings = Settings()
