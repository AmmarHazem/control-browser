from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "control-browser"
    host: str = "0.0.0.0"
    port: int = 8100
    log_level: str = "INFO"

    api_keys: list[str] = Field(default_factory=list)

    llm_model: str = "gpt-4o-mini"
    llm_base_url: str | None = None
    llm_api_key: str = ""

    searxng_url: str | None = None
    searxng_timeout: float = 10.0

    browser_headless: bool = True
    browser_locale: str = "en-US"
    browser_timezone: str = "UTC"
    browser_viewport_width: int = 1280
    browser_viewport_height: int = 900
    browser_user_agent: str | None = None

    max_sessions: int = 25
    memory_caution_percent: float = 75.0
    memory_high_percent: float = 85.0
    memory_critical_percent: float = 93.0
    session_idle_seconds: float = 300.0
    session_max_lifetime_seconds: float = 3600.0
    janitor_interval_seconds: float = 15.0

    action_timeout_seconds: float = 30.0
    action_timeout_cap_seconds: float = 120.0
    navigation_min_interval_seconds: float = 1.0

    snapshot_token_budget: int = 8000
    snapshot_token_cap: int = 32000
    text_token_budget: int = 16000
    text_token_cap: int = 64000

    run_max_steps: int = 50
    run_max_steps_cap: int = 200
    run_timeout_seconds: float = 600.0
    run_timeout_cap_seconds: float = 3600.0
    confirmation_timeout_seconds: float = 600.0
    confirmation_timeout_cap_seconds: float = 3600.0
    stuck_failure_threshold: int = 2

    blocked_domains: list[str] = Field(default_factory=list)
    allow_private_addresses: bool = False

    data_dir: Path = Path("data")
    screenshot_retention_seconds: float = 86400.0
    screenshot_sweep_interval_seconds: float = 3600.0

    @field_validator("api_keys", "blocked_domains", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def db_path(self) -> Path:
        return self.data_dir / "runs.db"

    @property
    def screenshot_dir(self) -> Path:
        return self.data_dir / "screenshots"


@lru_cache
def get_settings() -> Settings:
    return Settings()
