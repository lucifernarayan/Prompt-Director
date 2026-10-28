import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
    REDIS_URL: str = os.environ.get("REDIS_URL", "")
    WORKER_LEASE_SECONDS: int = int(os.environ.get("WORKER_LEASE_SECONDS", "15"))
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")


settings = Settings()

DATABASE_URL = settings.DATABASE_URL
REDIS_URL = settings.REDIS_URL
WORKER_LEASE_SECONDS = settings.WORKER_LEASE_SECONDS
LOG_LEVEL = settings.LOG_LEVEL
