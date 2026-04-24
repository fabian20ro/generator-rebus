# Puzzle Quality Roadmap

## Near Term

- Keep DEX as the primary definition source; use the committed answer supply only as labeled non-DEX context and fallback.
- Grow short two-letter answer coverage through curated sources that are available to both Rust generation and Python definition prompts.
- Evaluate prompt changes against the active baseline before keeping them.

## Answer Supply

- `answer_supply.json` is the shared source for approved non-DEX answer support.
- Approved entries can feed Rust grid generation, prompt context, and unresolved-definition rescue.
- Source priority: DEX, factual curated entries, colloquial curated entries, playful split entries, then LLM fallback.
- Romanian auto county codes are seeded as `curated_ro_plate`; one-letter `B` is excluded until one-letter answers are supported.
- Playful split entries stay separate, low priority, and use a visible `!` clue convention.

## Prompt Evaluation

- Use a 300-word target dataset: 100 easy, 100 medium, 100 hard.
- Prefer sampling from `run_all` word metrics so the benchmark reflects real production failures.
- Keep the current 70-word assessment as a smoke/control set.
- Compare baseline prompt snapshots to candidate snapshots with the same model pair and dataset.
- Track valid generation, guard rejection, verify pass, semantic, rebus, short-word pass, control regression, and truncation/parse failures.

## Promotion Rules

- Miner output is review-only until copied into `answer_supply.json` with `approved=true`.
- Curated entries must pass `validate_definition_text`.
- Prompt changes need measurable gains without material high/control regression.
