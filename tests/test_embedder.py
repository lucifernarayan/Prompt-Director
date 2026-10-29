import math
from app.memory.embedder import embed, cosine


def test_embedder_unit_length_and_dimension():
    text = "A neon-lit futuristic street with rain reflections"
    vec = embed(text)

    # Must be 64-dimensional
    assert len(vec) == 64

    # Must be L2-normalized (unit length within float precision)
    norm = math.sqrt(sum(x * x for x in vec))
    assert math.isclose(norm, 1.0, rel_tol=1e-5)


def test_embedder_determinism():
    text = "The same text must produce identical embeddings"
    vec1 = embed(text)
    vec2 = embed(text)
    assert vec1 == vec2


def test_embedder_similarity_monotonic_with_shared_tokens():
    base = "detective trench coat rain alley"
    similar = "detective trench coat neon city"  # 3 shared tokens
    less_similar = "detective spaceship laser galaxy"  # 1 shared token
    unrelated = "cooking recipe pasta tomato basil"  # 0 shared tokens

    v_base = embed(base)
    v_sim = embed(similar)
    v_less = embed(less_similar)
    v_unrelated = embed(unrelated)

    cos_sim = cosine(v_base, v_sim)
    cos_less = cosine(v_base, v_less)
    cos_unrelated = cosine(v_base, v_unrelated)

    assert cos_sim > cos_less, f"cos_sim={cos_sim} should exceed cos_less={cos_less}"
    assert cos_less > cos_unrelated, f"cos_less={cos_less} should exceed cos_unrelated={cos_unrelated}"
