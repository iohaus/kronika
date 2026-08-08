from __future__ import annotations

import os
from dataclasses import dataclass

_DATAHUB_HTTP_TIMEOUT_SECONDS_DEFAULT = 30.0


@dataclass(frozen=True)
class Settings:
    datahub_server_url: str = os.getenv("DATAHUB_SERVER_URL", "http://localhost:8080")
    datahub_token: str | None = os.getenv("DATAHUB_TOKEN", None)
    datahub_timeout_seconds: float = _DATAHUB_HTTP_TIMEOUT_SECONDS_DEFAULT
    duckdb_path: str = os.getenv("DUCKDB_PATH", ":memory:")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY", None)
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o")
    openai_base_url: str | None = os.getenv("OPENAI_BASE_URL", None)
    llm_timeout_seconds: float = float(os.getenv("KRONIKA_LLM_TIMEOUT_SECONDS", "30.0"))
    poll_interval_seconds: float = float(os.getenv("KRONIKA_POLL_INTERVAL_SECONDS", "60.0"))
    max_world_size: int = int(os.getenv("KRONIKA_MAX_WORLD_SIZE", "10000"))
    confidence_threshold: float = float(os.getenv("KRONIKA_CONFIDENCE_THRESHOLD", "0.90"))


settings = Settings()
