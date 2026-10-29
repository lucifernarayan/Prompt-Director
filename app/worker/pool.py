import os
import threading

MODEL_POOL_SIZE = int(os.environ.get("MODEL_POOL_SIZE", "4"))
_semaphore = threading.BoundedSemaphore(MODEL_POOL_SIZE)


def get_model_semaphore() -> threading.BoundedSemaphore:
    """Return the process-wide bounded semaphore for model calls."""
    return _semaphore


def set_model_semaphore(size: int) -> None:
    """Set the process-wide bounded semaphore size (used for testing and tuning)."""
    global _semaphore
    _semaphore = threading.BoundedSemaphore(size)
