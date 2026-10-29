import re
import unicodedata

# Common Cyrillic and Greek homoglyphs for a, e, o, p, c, x, y, i, s, k, h, t, m
HOMOGLYPH_MAP: dict[str, str] = {
    # Cyrillic
    "\u0430": "a",  # Cyrillic small a
    "\u0410": "a",
    "\u0435": "e",  # Cyrillic small e
    "\u0415": "e",
    "\u043e": "o",  # Cyrillic small o
    "\u041e": "o",
    "\u0440": "p",  # Cyrillic small er (p)
    "\u0420": "p",
    "\u0441": "c",  # Cyrillic small es (c)
    "\u0421": "c",
    "\u0445": "x",  # Cyrillic small ha (x)
    "\u0425": "x",
    "\u0443": "y",  # Cyrillic small u (y)
    "\u0423": "y",
    "\u0456": "i",  # Cyrillic small byelorussian-ukrainian i
    "\u0406": "i",
    "\u0455": "s",  # Cyrillic small dze (s)
    "\u0405": "s",
    "\u043a": "k",  # Cyrillic small ka
    "\u041a": "k",
    "\u04bb": "h",  # Cyrillic small shha (h)
    "\u04ba": "h",
    "\u0442": "t",  # Cyrillic small te
    "\u0422": "t",
    "\u043c": "m",  # Cyrillic small em
    "\u041c": "m",
    # Greek
    "\u03b1": "a",  # Greek small alpha
    "\u0391": "a",
    "\u03b5": "e",  # Greek small epsilon
    "\u0395": "e",
    "\u03bf": "o",  # Greek small omicron
    "\u039f": "o",
    "\u03c1": "p",  # Greek small rho
    "\u03a1": "p",
    "\u03b9": "i",  # Greek small iota
    "\u0399": "i",
    "\u03ba": "k",  # Greek small kappa
    "\u039a": "k",
    "\u03c4": "t",  # Greek small tau
    "\u03a4": "t",
    "\u03bc": "m",  # Greek small mu
    "\u039c": "m",
    "\u03c7": "x",  # Greek small chi
    "\u03a7": "x",
    "\u03c5": "y",  # Greek small upsilon
    "\u03a5": "y",
    "\u03bd": "v",  # Greek small nu
    "\u039d": "v",
}


def collapse_single_letter_runs(tokens: list[str]) -> list[str]:
    """Collapse runs of 3+ single-letter tokens ('t', 'o', 'm' -> 'tom')."""
    out: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        if len(tokens[i]) == 1:
            j = i
            while j < n and len(tokens[j]) == 1:
                j += 1
            if j - i >= 3:
                out.append("".join(tokens[i:j]))
                i = j
            else:
                while i < j:
                    out.append(tokens[i])
                    i += 1
        else:
            out.append(tokens[i])
            i += 1
    return out


def normalize_text(text: str) -> list[str]:
    """Normalize input text in strict order and return token list.

    1. NFKC normalization
    2. Casefold
    3. Remove Unicode category Cf (zero-width, bidi, joiners) and Cc (control chars)
    4. Homoglyph mapping (Cyrillic/Greek lookalikes to Latin)
    5. Replace non-alphanumeric with space
    6. Collapse whitespace and collapse runs of 3+ single-letter tokens
    """
    if not isinstance(text, str):
        raise TypeError("Input text must be a string")

    # 1. NFKC
    nfkc = unicodedata.normalize("NFKC", text)

    # 2. Casefold
    cf = nfkc.casefold()

    # 3. Remove Cf and Cc
    no_cf_cc = "".join(c for c in cf if unicodedata.category(c) not in ("Cf", "Cc"))

    # 4. Homoglyphs
    homo = "".join(HOMOGLYPH_MAP.get(c, c) for c in no_cf_cc)

    # 5. Replace non-alphanumeric with space (keep spaces to preserve multi-space boundaries)
    alnum = "".join(c if c.isalnum() else " " for c in homo)

    # 6. Preserve multi-space boundaries ("t o m  c r u i s e") by chunking on 2+ spaces
    chunks = re.split(r"[ ]{2,}", alnum)
    final_tokens: list[str] = []

    for chunk in chunks:
        raw_tokens = chunk.split()
        if raw_tokens:
            collapsed = collapse_single_letter_runs(raw_tokens)
            final_tokens.extend(collapsed)

    return final_tokens
