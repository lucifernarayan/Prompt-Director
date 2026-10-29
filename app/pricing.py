"""Pricing definitions for Smart Prompt Director.

This is the only place prices and provider rates live.
All monetary amounts are strictly integer micro-credits (never floats).
"""

# Fixed deterministic quotes by request type (in micro-credits)
# Full prompt generation quote: 100,000 micro-credits
FULL_QUOTE: int = 100_000

# Lower quote for single-axis edit: 40,000 micro-credits
AXIS_EDIT_QUOTE: int = 40_000

# Provider-cost cap ratio: 80% of quote
COST_CAP_PERCENT: int = 80

# Default provider cost cap for full quote: 80,000 micro-credits (80% of 100,000)
COST_CAP: int = (FULL_QUOTE * COST_CAP_PERCENT) // 100

# Fixed worst-case token counts per provider call used for pre-call cap checks
# Cheap model worst-case: 1,000 input tokens, 500 output tokens
CHEAP_WORST_CASE_TOKENS_IN: int = 1_000
CHEAP_WORST_CASE_TOKENS_OUT: int = 500

# Strong model worst-case: 1,000 input tokens, 500 output tokens
STRONG_WORST_CASE_TOKENS_IN: int = 1_000
STRONG_WORST_CASE_TOKENS_OUT: int = 500

# Per-token provider rates in micro-credits
# Cheap model: 5 micro-credits per input token, 10 micro-credits per output token
# Cheap worst-case cost = (1,000 * 5) + (500 * 10) = 10,000 micro-credits
CHEAP_INPUT_RATE_MICRO: int = 5
CHEAP_OUTPUT_RATE_MICRO: int = 10

# Strong model: 30 micro-credits per input token, 80 micro-credits per output token
# Strong worst-case cost = (1,000 * 30) + (500 * 80) = 70,000 micro-credits
STRONG_INPUT_RATE_MICRO: int = 30
STRONG_OUTPUT_RATE_MICRO: int = 80

# Verification of invariant constraints:
# (a) Single strong call at worst case (70,000) <= COST_CAP (80,000) -> Fits inside cap
# (b) cheap + cheap + strong at worst case (10,000 + 10,000 + 70,000 = 90,000) > COST_CAP (80,000) -> Exceeds cap


def get_cost_cap(quote_micro: int) -> int:
    """Return 80% of the given quote in integer micro-credits."""
    return (quote_micro * COST_CAP_PERCENT) // 100


def calculate_cost_micro(model: str, tokens_in: int, tokens_out: int) -> int:
    """Calculate actual provider cost for a call in integer micro-credits."""
    if model == "strong":
        return (tokens_in * STRONG_INPUT_RATE_MICRO) + (tokens_out * STRONG_OUTPUT_RATE_MICRO)
    # Default to cheap model rates
    return (tokens_in * CHEAP_INPUT_RATE_MICRO) + (tokens_out * CHEAP_OUTPUT_RATE_MICRO)


def cost_of_attempt(model: str, tokens_in: int, tokens_out: int, outcome: str) -> int:
    """Compute cost of a provider attempt in integer micro-credits.

    - outcome 'rate_limit' (429) costs 0.
    - outcome 'timeout' costs tokens_in * input_rate only.
    - every other outcome costs tokens_in * in_rate + tokens_out * out_rate.
    """
    in_rate = STRONG_INPUT_RATE_MICRO if model == "strong" else CHEAP_INPUT_RATE_MICRO
    out_rate = STRONG_OUTPUT_RATE_MICRO if model == "strong" else CHEAP_OUTPUT_RATE_MICRO

    if outcome == "rate_limit":
        return 0
    elif outcome == "timeout":
        return tokens_in * in_rate
    return (tokens_in * in_rate) + (tokens_out * out_rate)


def worst_case_cost(model: str) -> int:
    """Return worst-case cost for a model call."""
    if model == "strong":
        return (STRONG_WORST_CASE_TOKENS_IN * STRONG_INPUT_RATE_MICRO) + (
            STRONG_WORST_CASE_TOKENS_OUT * STRONG_OUTPUT_RATE_MICRO
        )
    return (CHEAP_WORST_CASE_TOKENS_IN * CHEAP_INPUT_RATE_MICRO) + (
        CHEAP_WORST_CASE_TOKENS_OUT * CHEAP_OUTPUT_RATE_MICRO
    )
