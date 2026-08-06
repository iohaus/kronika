from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    datahub_server_url: str = os.getenv("DATAHUB_SERVER_URL", "http://localhost:8080")
    datahub_token: str | None = os.getenv("DATAHUB_TOKEN", None)
    datahub_timeout_seconds: float = float(os.getenv("KRONIKA_LLM_TIMEOUT_SECONDS", "30.0"))
    max_world_size: int = int(os.getenv("KRONIKA_MAX_WORLD_SIZE", "10000"))
    confidence_threshold: float = float(os.getenv("KRONIKA_CONFIDENCE_THRESHOLD", "0.90"))


settings = Settings()
