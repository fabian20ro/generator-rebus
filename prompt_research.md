# Prompt Optimization Program

> Autoresearch-inspired optimization loop for Romanian crossword definition prompts.

## Goal

Maximize the **composite assessment score**:

```
composite = pass_rate × 100 + avg_semantic × 3 + avg_rebus × 2
```

- `pass_rate`: fraction of definitions where the verifier guesses the exact word
- `avg_semantic`: average semantic correctness score (1-10)
- `avg_rebus`: average rebus score (0.75 × guessability + 0.25 × creativity)

Higher = better. Theoretical maximum ≈ 130.

## Editable Files (THE target)

| File | Role |
|------|------|
| `generator/prompts/system/definition.md` | System prompt: how to generate definitions |
| `generator/prompts/system/verify.md` | System prompt: how to guess words from definitions |
| `generator/prompts/system/rate.md` | System prompt: how to rate definitions |
| `generator/prompts/system/rewrite.md` | System prompt: how to rewrite failed definitions |
| `generator/prompts/system/clue_tiebreaker.md` | System prompt: choose between two definitions |
| `generator/prompts/system/puzzle_tiebreaker.md` | System prompt: choose between two puzzles |
| `generator/prompts/user/generate.md` | User template: initial clue generation |
| `generator/prompts/user/verify.md` | User template: verification prompt |
| `generator/prompts/user/rate.md` | User template: rating prompt |
| `generator/prompts/user/rewrite.md` | User template: rewrite prompt |
| `generator/prompts/user/clue_tiebreak.md` | User template: clue comparison |
| `generator/prompts/user/puzzle_tiebreak.md` | User template: puzzle comparison |

## Read-Only Files (DO NOT MODIFY)

| File | Role |
|------|------|
| `generator/assessment/dataset.json` | Current multistep assessment set built from March-17 batch mining |
| `generator/assessment/run_assessment.py` | Assessment runner (composite metric computation) |
| `generator/assessment/multistep_results.tsv` | Experiment log (append-only) |

## Running an Experiment

```bash
# 1. Edit a prompt file
# 2. Run assessment
python3 -m generator.assessment.run_assessment --description "short description of change"

# 3. Check results
cat generator/assessment/multistep_results.tsv

# 4. If improved: commit and keep
# 5. If regressed: revert
git checkout -- generator/prompts/  # revert prompt changes
```

## Constraints

- **Language**: All prompts must be in Romanian
- **Definition length**: Max 12 words per definition
- **No word-specific hacks**: Prompts must be generic (no hardcoded word lists)
- **One variable at a time**: Change one prompt aspect per experiment
- **Per-tier monitoring**: Check low/medium/high buckets and short-word behavior — no regressions on stable high-score controls
- **Model compatibility**: Prompts must work with both gpt-oss-20b and eurollm-22b (7B-22B local models need more structure than cloud APIs)

## Known Insights from Batch Log Analysis

These are patterns observed in `20260317_002435/run.log` that should guide experiments:

1. **Length enforcement in verify**: The verifier often ignores answer length. Adding "EXACT {N} litere" and letter-counting instructions improved accuracy in manual tests.

2. **JSON compliance in rate**: eurollm-22b wraps JSON in markdown fences or adds preamble text. A concrete example output in the system prompt reduced parse failures.

3. **Crossword-style vs. dictionary-style definitions**: "Solo pe scenă" (crossword) >> "Concert cu un singur interpret" (dictionary) for guessability. Prompts should emphasize crossword brevity.

4. **Short-word specialization (2-3 letters)**: Short words like AR, LA, IN have extremely low pass rates because definitions are inherently ambiguous at that length. These need different strategies:
   - Use word-type hints (prepoziție, interjecție, etc.)
   - Reference specific Romanian usage contexts
   - Avoid definitions that could match any short word

5. **Anti-hallucination for rare words**: Rare words (rarity ≥ 3) trigger hallucinated definitions. Providing DEX definitions as reference material reduces this.

6. **Failure history prevents loops**: Without seeing previous failed attempts, the rewriter often produces the same definition again. The failure_history parameter addresses this.

## Assessment Dataset Tiers

| Tier | Count | Criteria | Purpose |
|------|-------|----------|---------|
| Low | 30 | avg rebus < 5 from `20260317_*` | Primary failure target |
| Medium | 25 | avg rebus 6-7, repeated, low variance | Main optimization target |
| High | 15 | avg rebus 9-10, stable controls | Regression guard |

## Experiment Log Convention

Each row in `multistep_results.tsv`:
```
commit  composite  pass_rate  avg_semantic  avg_rebus  status  description
```

- `status`: `keep` (merged) or `discard` (reverted)
- Keep descriptions short and specific: "add JSON example to rate prompt", "add letter-count instruction to verify"
