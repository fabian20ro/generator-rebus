"""Prompt benchmark policy derived from historical ledgers, especially results4.tsv."""

from __future__ import annotations


INCUMBENT_COMPOSITE = 74.2
BASELINE_FLOOR_COMPOSITE = 72.0
UNCERTAINTY_DELTA = 0.5

# Protected keep-set: historically helpful or too costly to remove.
PROTECTED_KEEP_SET = {
    "verify": [
        "length scaffold",
        "technical/rare hint",
        "domain-flex hint",
        "word-type line",
    ],
    "rewrite": [
        "failure history context",
        "bad example context",
    ],
    "rate": [
        "normalized-form line",
        "positive creativity framing",
    ],
}

# Themes that should not be retried in the next campaign unless the objective changes.
HARD_DISCARD_SET = [
    "common-word bias",
    "expert framing",
    "extra length nagging",
    "exact-sense abstract guards",
    "DEX-heavy rating anchors/examples",
    "generate-user meta-instructions",
    "test-question framing",
]

# Near-neutral removals worth reconsidering once the refactored benchmark is stable.
BORDERLINE_CLEANUP_BUCKET = [
    "duplicate verify length reminder",
    "rewrite more-precise-than-old-clue line",
    "generate final instruction simplification",
]

# Prefer examples and counterexamples over abstract rule accretion.
PROMPT_EXPERIMENT_PRIORITIES = [
    "verify examples",
    "rewrite elimination using failed guesses",
    "rate broad-but-correct counterexamples",
    "definition anti-vague negative examples",
]
