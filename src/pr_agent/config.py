"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the PR review agent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    github_app_id: str = ""
    github_webhook_secret: str = ""
    github_private_key: str = ""
    github_private_key_path: str = ""
    github_api_base_url: str = "https://api.github.com"

    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/pr_agent"
    queue_backend: Literal["memory", "redis"] = "memory"

    review_trigger_command: str = "/review"

    gemini_api_key: str = ""
    gemini_triage_model: str = "gemini-3.8-flash"
    gemini_review_model: str = "gemini-3.8-flash"

    def resolve_private_key(self) -> str:
        """Return the PEM private key from env string or file path."""
        if self.github_private_key:
            return self.github_private_key.replace("\\n", "\n")
        if self.github_private_key_path:
            return Path(self.github_private_key_path).read_text(encoding="utf-8")
        raise ValueError("Neither GITHUB_PRIVATE_KEY nor GITHUB_PRIVATE_KEY_PATH is configured.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()

