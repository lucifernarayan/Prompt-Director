from app.memory.grounding import ground, has_ungrounded, hallucination_rate


def test_t2_fake_shot_id_removed():
    retrieved = [{"id": "S1", "kind": "shot"}, {"id": "C1", "kind": "character"}]
    raw_val = "scene continuity reference [shot:S99]"

    new_val, citations, removed = ground(raw_val, retrieved)
    assert new_val == "none"
    assert citations == []
    assert len(removed) == 1
    assert "S99" in removed[0]


def test_t2_mixed_value_keeps_only_grounded_claims():
    retrieved = [{"id": "S1", "kind": "shot"}, {"id": "P2", "kind": "prop"}]
    raw_val = (
        "detective enters alley [shot:S1]; "
        "phantom spinner appears [shot:S99]; "
        "carrying memory crystal [prop:P2]"
    )

    new_val, citations, removed = ground(raw_val, retrieved)
    assert "detective enters alley [shot:S1]" in new_val
    assert "carrying memory crystal [prop:P2]" in new_val
    assert "phantom spinner appears [shot:S99]" not in new_val

    assert ("shot", "S1") in citations
    assert ("prop", "P2") in citations
    assert len(citations) == 2

    assert len(removed) == 1
    assert "S99" in removed[0]


def test_t2_all_ungrounded_becomes_none():
    retrieved = [{"id": "S1", "kind": "shot"}]
    # Claims without any citations or with invalid citations
    raw_val = "unreferenced claim without tags; phantom prop [prop:P99]"

    new_val, citations, removed = ground(raw_val, retrieved)
    assert new_val == "none"
    assert citations == []
    assert len(removed) == 2


def test_t2_foreign_project_item_id_is_ungrounded():
    # S1 from proj_a is retrieved; C1 from proj_b is NOT retrieved
    retrieved_proj_a = [{"id": "S1", "kind": "shot"}]
    raw_val = "hacker maya from proj b arrives [character:C1]; neon street [shot:S1]"

    new_val, citations, removed = ground(raw_val, retrieved_proj_a)
    assert "neon street [shot:S1]" in new_val
    assert "hacker maya" not in new_val
    assert citations == [("shot", "S1")]
    assert len(removed) == 1


def test_t2_has_ungrounded_flag():
    retrieved = [{"id": "S1", "kind": "shot"}]
    raw_val = "phantom reference [shot:S99]"
    assert has_ungrounded(raw_val, retrieved) is True

    new_val, _, _ = ground(raw_val, retrieved)
    assert has_ungrounded(new_val, retrieved) is False


def test_t2_hallucination_rate_helper():
    retrieved = [{"id": "S1", "kind": "shot"}, {"id": "P1", "kind": "prop"}]
    sample_values = [
        "detective in rain [shot:S1]",             # Grounded
        "carrying blaster [prop:P1]",             # Grounded
        "phantom spinner [shot:S99]",             # Hallucinated
        "mysterious stranger [character:C99]",    # Hallucinated
    ]

    rates = hallucination_rate(sample_values, retrieved)
    assert rates["before"] == 0.5   # 2 out of 4 have ungrounded claims
    assert rates["after"] == 0.0    # 0 ungrounded claims after grounding
