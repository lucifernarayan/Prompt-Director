import hashlib
import math


def embed(text: str) -> list[float]:
    """Pure-Python deterministic feature hashing into 64 dimensions, L2-normalized.

    Splits text on whitespace into lowercase tokens, hashes each token with sha256
    into one of 64 dimensions with a sign bit, then normalizes to unit length.
    """
    if not text or not isinstance(text, str):
        return [0.0] * 64

    tokens = text.lower().split()
    if not tokens:
        return [0.0] * 64

    vec = [0.0] * 64
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        dim = digest[0] % 64
        sign = 1.0 if (digest[1] & 1) == 0 else -1.0
        vec[dim] += sign

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0.0:
        return [x / norm for x in vec]
    return [0.0] * 64


def cosine(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two unit vectors (dot product)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))
