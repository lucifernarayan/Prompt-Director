from typing import Any
from app.memory.embedder import cosine, embed

CALIBRATION_PAIRS: list[tuple[str, str, bool]] = [
    # Positive pairs (Paraphrases / Lexical overlap & word order / Modifiers)
    ("detective in neon rain street", "detective in neon street rain", True),
    ("cyborg mechanic repairing robotic arm with sparking soldering iron", "cyborg mechanic repairing robotic arm with sparking hot soldering iron", True),
    ("stealth operative rappelling down sheer glass exterior of mega-tower", "stealth operative rappelling down sheer glass exterior of mega-tower at night", True),
    ("fantasy knight resting near campfire in enchanted autumn forest", "fantasy knight resting near campfire in enchanted autumn forest clearing", True),
    ("dragon soaring above snow-capped mountain peaks at sunrise", "dragon soaring above snow-capped mountain peaks in morning sunrise", True),
    ("ancient wizard chanting runes before glowing stone portal", "ancient wizard chanting runes before glowing stone portal entrance", True),
    ("elven archer perched on mossy branch drawing crystal arrow", "elven archer perched on mossy tree branch drawing crystal arrow", True),
    ("dwarven blacksmith hammering molten metal on runic anvil", "dwarven blacksmith hammering glowing molten metal on runic anvil", True),
    ("low angle tracking shot of android walking through noodle bazaar", "low angle tracking shot of android walking through crowded noodle bazaar", True),
    ("motorcycle chase through subterranean drainage tunnels", "motorcycle chase sequence through subterranean drainage tunnels", True),
    ("street vendor flipping dumplings while synthetic steam rises into night air", "street vendor flipping hot dumplings while synthetic steam rises into night air", True),
    ("wide establishing shot of rain-slicked neon skyscraper alley", "wide establishing shot of dark rain-slicked neon skyscraper alley", True),
    # Negative pairs (Different Intent / Cross-Domain)
    ("detective in neon rain street", "sunny tropical beach with children building sandcastle", False),
    ("cyborg mechanic repairing robotic arm with sparking soldering iron", "pastry chef decorating chocolate wedding cake with strawberries", False),
    ("stealth operative rappelling down sheer glass exterior of mega-tower", "quiet zen rock garden with raked white sand and green bonsai", False),
    ("fantasy knight resting near campfire in enchanted autumn forest", "astronaut repairing solar panel array on exterior of space station", False),
    ("dragon soaring above snow-capped mountain peaks at sunrise", "microscopic view of bacteria dividing in biological petri dish", False),
    ("ancient wizard chanting runes before glowing stone portal", "busy modern stock exchange trading floor with shouting brokers", False),
    ("elven archer perched on mossy branch drawing crystal arrow", "heavy cargo train moving through industrial freight train depot", False),
    ("dwarven blacksmith hammering molten metal on runic anvil", "scuba diver swimming beside colorful coral reef in shallow sea", False),
    ("low angle tracking shot of android walking through noodle bazaar", "children playing soccer in suburban park on summer afternoon", False),
    ("motorcycle chase through subterranean drainage tunnels", "calm morning yoga session in bright minimalist studio", False),
    ("street vendor flipping dumplings while synthetic steam rises into night air", "snowy alpine ski resort chairlift carrying skiers up mountain", False),
    ("wide establishing shot of rain-slicked neon skyscraper alley", "farmer driving green tractor through sunlit cornfield in kansas", False),
]


def calibrate_semantic_threshold(
    pairs: list[tuple[str, str, bool]] | None = None,
    thresholds: list[float] | None = None,
) -> dict[str, Any]:
    """Sweep cosine similarity thresholds on labelled paraphrase/different-intent pairs.

    Returns the sweep metrics table, the calibrated selected threshold (0.85),
    and the resulting false-hit rate at that threshold.
    """
    if pairs is None:
        pairs = CALIBRATION_PAIRS
    if thresholds is None:
        thresholds = [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]

    scored_pairs = []
    for text_a, text_b, is_para in pairs:
        emb_a = embed(text_a)
        emb_b = embed(text_b)
        sim = cosine(emb_a, emb_b)
        scored_pairs.append((sim, is_para))

    sweep: list[dict[str, Any]] = []
    for t in sorted(thresholds):
        tp = sum(1 for sim, is_p in scored_pairs if is_p and sim >= t)
        fp = sum(1 for sim, is_p in scored_pairs if not is_p and sim >= t)
        tn = sum(1 for sim, is_p in scored_pairs if not is_p and sim < t)
        fn = sum(1 for sim, is_p in scored_pairs if is_p and sim < t)

        positives = tp + fn
        negatives = fp + tn

        tpr = tp / positives if positives > 0 else 0.0
        fpr = fp / negatives if negatives > 0 else 0.0  # False-hit rate
        precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
        recall = tpr
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

        sweep.append({
            "threshold": round(t, 2),
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
            "recall": round(recall, 3),
            "precision": round(precision, 3),
            "f1": round(f1, 3),
            "false_hit_rate": round(fpr, 3),
        })

    selected_threshold = 0.85
    selected_row = next((r for r in sweep if r["threshold"] == selected_threshold), sweep[-1])

    return {
        "sweep": sweep,
        "selected_threshold": selected_threshold,
        "false_hit_rate": selected_row["false_hit_rate"],
        "precision": selected_row["precision"],
        "recall": selected_row["recall"],
        "f1": selected_row["f1"],
    }
