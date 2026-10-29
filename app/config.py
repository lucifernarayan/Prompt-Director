import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
    REDIS_URL: str = os.environ.get("REDIS_URL", "")
    WORKER_LEASE_SECONDS: int = int(os.environ.get("WORKER_LEASE_SECONDS", "15"))
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
    TIME_SCALE: float = float(os.environ.get("TIME_SCALE", "1.0"))
    RATE_LIMIT_REQUESTS: int = int(os.environ.get("RATE_LIMIT_REQUESTS", "60"))
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))


settings = Settings()

DATABASE_URL = settings.DATABASE_URL
REDIS_URL = settings.REDIS_URL
WORKER_LEASE_SECONDS = settings.WORKER_LEASE_SECONDS
LOG_LEVEL = settings.LOG_LEVEL
TIME_SCALE = settings.TIME_SCALE
RATE_LIMIT_REQUESTS = settings.RATE_LIMIT_REQUESTS
RATE_LIMIT_WINDOW_SECONDS = settings.RATE_LIMIT_WINDOW_SECONDS
