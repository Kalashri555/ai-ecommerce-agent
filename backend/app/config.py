"""Application configuration.

All settings come from environment variables (or a `.env` file in the project
root / backend folder). Nothing secret is ever hard-coded here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent  # .../backend
PROJECT_ROOT = BACKEND_DIR.parent  # .../ai-ecommerce-agent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_ROOT / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenAI -------------------------------------------------------------
    openai_api_key: str = ""
    openai_model: str = ""
    openai_timeout_seconds: float = 60.0
    openai_max_retries: int = 2  # bounded retries for transient API errors

    # --- Database -----------------------------------------------------------
    database_url: str = "sqlite:///./ecommerce.db"

    # --- Web ----------------------------------------------------------------
    frontend_origin: str = "http://localhost:5173"
    app_env: str = "development"

    # --- Agent --------------------------------------------------------------
    max_tool_rounds: int = Field(default=8, ge=1, le=20)
    max_tool_calls_per_turn: int = Field(default=20, ge=1, le=60)
    history_message_limit: int = Field(default=12, ge=2, le=50)
    tool_timeout_seconds: float = 20.0
    max_tool_result_chars: int = 12000

    # --- Security -----------------------------------------------------------
    # Used to sign login tokens and the user context the backend sends to the
    # MCP servers. CHANGE THIS outside local development.
    app_secret_key: str = "dev-only-change-me-please-0123456789"
    token_ttl_minutes: int = 12 * 60
    approval_ttl_minutes: int = 15
    chat_rate_limit_per_minute: int = 20

    # --- MCP ----------------------------------------------------------------
    mcp_startup_timeout_seconds: float = 30.0

    @field_validator("database_url")
    @classmethod
    def _resolve_sqlite_path(cls, value: str) -> str:
        """Make relative SQLite paths absolute (relative to backend/).

        The MCP servers run as separate processes with their own working
        directory, so every process must point at the *same* file.
        """
        prefix = "sqlite:///"
        if value.startswith(prefix) and not value.startswith("sqlite:////"):
            raw = value[len(prefix):]
            if raw and raw != ":memory:" and not Path(raw).is_absolute():
                return prefix + str((BACKEND_DIR / raw).resolve()).replace("\\", "/")
        return value

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
