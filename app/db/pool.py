from contextlib import contextmanager
from typing import Generator
import psycopg
from psycopg_pool import ConnectionPool

from app.config import settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = ConnectionPool(
            conninfo=settings.DATABASE_URL,
            min_size=1,
            max_size=10,
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None and not _pool.closed:
        _pool.close()
        _pool = None


class _PoolProxy:
    """Proxy that forwards attribute lookups to the singleton ConnectionPool."""

    def __getattr__(self, name: str):
        return getattr(get_pool(), name)

    def connection(self, *args, **kwargs):
        return get_pool().connection(*args, **kwargs)

    def close(self) -> None:
        close_pool()

    def open(self, *args, **kwargs) -> None:
        get_pool().open(*args, **kwargs)


pool = _PoolProxy()


@contextmanager
def get_connection() -> Generator[psycopg.Connection, None, None]:
    with get_pool().connection() as conn:
        yield conn
