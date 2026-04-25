# Puzzle Quality Roadmap

## Evidence
- `run_all` healthy: Apr 24 run clean.
- Storage clean: Audit ok after canonical cleanup.
- Clue signal bottleneck: Eval/verifier misses.
- Weak spots: Short words, rare/tech, generic defs, family leakage, abstract titles.
- Missing: Reviewed Romanian puzzle corpus.

## Priorities
1. Gold corpus path + review UI.
2. Corpus into verifier/definition benchmarks.
3. Fix verifier misses via tests/prompts.
4. Short-word support: Examples, context, scoring. No blocklist.
5. Gemma truncation/reasoning cleanup.
6. Retitles (after clue pass improves).

## Gold Corpus
### Format
- Folder/puzzle. 2 photos/folder.
- `definitions.jpg`, `solution.jpg`, `meta.json` (optional).
- Flat paired filenames OK.

### Layout
- Raw: `build/gold_ingestion/raw/`
- Work: `build/gold_ingestion/work/`
- Approved: `build/gold_ingestion/approved.jsonl`
- Fixtures: Source-controlled post-review.

### JSONL Shape
- Line = puzzle.
- ID, source, size, grid, clues (number, direction, answer, original, def, confidence).

### Review UI
- Upload/preprocess (rotate, crop, contrast).
- OCR definitions -> numbered text.
- Detect grid -> letters -> start/direction.
- Align defs to answers.
- Review: Images + OCR boxes + grid overlay + clue table + keyboard edits.
- Export JSONL (only full records).

## Answer Supply
- `answer_supply.json` = shared non-DEX source.
- Approved entries -> Rust gen, prompt context, rescue.
- Priority: DEX, curated factual, curated colloquial, playful splits, LLM.
- Plate codes: `curated_ro_plate`. `B` excluded until 1-letter answers supported.
- Playful splits: Low priority, `!` convention.
- Promotion: Manual review only.

## Prompt Evaluation
- Dataset: 300 words (100 easy/medium/hard).
- Sample `run_all` metrics (real failures).
- Smoke set: 70-word control.
- Gold fixtures: Separate, not mixed in.
- Comparison: Fixed model pair + dataset.
- Metrics: Gen, guard, verify, semantic, rebus, short-word, regression, truncation/parse.

## Rules
- OCR = draft until reviewed.
- Miner = review until `approved=true` in `answer_supply.json`.
- Entry must pass `validate_definition_text`.
- Prompt: Gain without regression.
- Gold: Preserve meta, no solution leakage.

## Test
- JSONL schema validation.
- Tiny gold fixture for import/export tests.
- Gate: Human review correctness.
- Test benchmark imports (verifier + definition examples).
- Verifier tests for `run_all` misses before prompt changes.
