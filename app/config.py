"""
config.py — Configuración por variables de entorno (un solo lugar).
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    redis_url: str = "redis://localhost:6379"
    # Cuánto viven en Redis el estado del job y los checkpoints (después se borran solos)
    job_ttl_seconds: int = 60 * 60 * 24
    checkpoint_ttl_minutes: int = 60 * 24
    # Máximo de jobs ejecutándose a la vez en este proceso (el resto queda en PENDING)
    max_concurrent_jobs: int = 10
    # Si un job tarda más que esto se corta y queda en FAILED (nadie queda esperando para siempre)
    job_timeout_seconds: int = 300
    # "phoenix" | "langsmith" | "none"
    observability: str = "phoenix"
    phoenix_endpoint: str = "http://localhost:6006"
    phoenix_project: str = "multi-agent-api"

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.getenv
        return cls(
            redis_url=e("REDIS_URL", cls.redis_url),
            job_ttl_seconds=int(e("JOB_TTL_SECONDS", cls.job_ttl_seconds)),
            checkpoint_ttl_minutes=int(e("CHECKPOINT_TTL_MINUTES", cls.checkpoint_ttl_minutes)),
            max_concurrent_jobs=int(e("MAX_CONCURRENT_JOBS", cls.max_concurrent_jobs)),
            job_timeout_seconds=int(e("JOB_TIMEOUT_SECONDS", cls.job_timeout_seconds)),
            observability=e("OBSERVABILITY", cls.observability).lower(),
            phoenix_endpoint=e("PHOENIX_COLLECTOR_ENDPOINT", cls.phoenix_endpoint).rstrip("/"),
            phoenix_project=e("PHOENIX_PROJECT_NAME", cls.phoenix_project),
        )
