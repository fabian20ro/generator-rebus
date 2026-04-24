# Puzzle Quality Roadmap

## Current Evidence

- `run_all` infrastructure is healthy: active scheduler steps completed without failed/quarantined units in the April 24 run.
- Storage integrity is mostly clean: puzzle definition audit found no missing slots, blank definitions, duplicate slots, orphan clues, or puzzle count mismatches after touched canonical cleanup.
- Quality bottleneck is clue signal, not persistence: many generate/redefine candidates still get incomplete pair evaluation or verifier near-misses.
- Repeated weak spots: short 2-3 letter answers, rare/technical words, generic dictionary-like definitions, family-word leakage retries, and abstract retitles.
- Missing input: no reviewed corpus of real Romanian puzzle clue style.

## Near Term Priorities

1. Build a real-puzzle gold corpus ingestion path with a local review UI.
2. Convert reviewed corpus examples into verifier and definition benchmark fixtures.
3. Mine current `run_all` verifier misses into targeted tests and prompt experiments.
4. Improve short-word support using reviewed examples, curated context, and scoring; never blocklist Romanian words.
5. Clean rewrite artifacts and Gemma truncation/reasoning policy.
6. Improve retitles only after clue pass rate improves.

## Real Puzzle Gold Corpus

### Input Format

Preferred layout: one folder per puzzle, two photos per folder.

```text
incoming/rebus_0001/
  definitions.jpg
  solution.jpg
  meta.json
```

`meta.json` is optional:

```json
{
  "source": "book/newspaper/app name",
  "issue": "",
  "page": "",
  "date": "",
  "notes": ""
}
```

Flat paired filenames are also accepted:

```text
rebus_0001_definitions.jpg
rebus_0001_solution.jpg
rebus_0002_definitions.jpg
rebus_0002_solution.jpg
```

### Artifact Layout

- Raw imported files: `build/gold_ingestion/raw/`
- OCR/preprocess/review drafts: `build/gold_ingestion/work/`
- Human-approved corpus: `build/gold_ingestion/approved.jsonl`
- Promotion into source-controlled fixtures happens separately after review.

### Approved JSONL Shape

Each line is one reviewed puzzle:

```json
{
  "puzzle_id": "rebus_0001",
  "source": "manual_photo",
  "size": 13,
  "grid": [
    "CASA#MARE####",
    "..."
  ],
  "clues": [
    {
      "number": 1,
      "direction": "V",
      "answer": "CASA",
      "answer_original": "casă",
      "definition": "Locuință pentru oameni.",
      "confidence": "reviewed"
    }
  ]
}
```

Intermediate OCR records may include bounding boxes, crops, raw OCR text, and alignment confidence. Approved records stay compact: answer, direction, definition, grid, source.

### Local Review UI

- Upload/select `definitions.jpg` and `solution.jpg`.
- Preprocess images: rotate, crop, deskew, contrast.
- OCR the definitions page into numbered clue text.
- Detect solved grid, read letters, infer horizontal/vertical answer starts.
- Align definitions to answers by number and direction.
- Review screen:
  - original images with OCR boxes
  - solved grid overlay
  - clue table with `number`, `direction`, `answer`, `definition`
  - keyboard edits for OCR text, answer fixes, and direction swaps
- Export JSONL only after every clue has answer and definition.

## Answer Supply

- `answer_supply.json` remains the shared source for approved non-DEX answer support.
- Approved entries can feed Rust grid generation, prompt context, and unresolved-definition rescue.
- Source priority: DEX, factual curated entries, colloquial curated entries, playful split entries, then LLM fallback.
- Romanian auto county codes are seeded as `curated_ro_plate`; one-letter `B` is excluded until one-letter answers are supported.
- Playful split entries stay separate, low priority, and use a visible `!` clue convention.
- Real-puzzle examples do not auto-promote to `answer_supply.json`; promote only reviewed reusable entries that pass validation.

## Prompt Evaluation

- Use a 300-word target dataset: 100 easy, 100 medium, 100 hard.
- Prefer sampling from `run_all` word metrics so the benchmark reflects real production failures.
- Keep the current 70-word assessment as a smoke/control set.
- Add reviewed real-puzzle examples as separate gold fixtures, not mixed silently into the active benchmark.
- Compare baseline prompt snapshots to candidate snapshots with the same model pair and dataset.
- Track valid generation, guard rejection, verify pass, semantic, rebus, short-word pass, control regression, and truncation/parse failures.
- Gold corpus imports must produce both `definition -> answer` verifier examples and `answer -> definition` definition examples.

## Promotion Rules

- OCR output is draft-only until human-reviewed.
- Miner output is review-only until copied into `answer_supply.json` with `approved=true`.
- Curated entries and approved gold clues must pass `validate_definition_text`.
- Prompt changes need measurable gains without material high/control regression.
- New real-puzzle fixtures must preserve source metadata and avoid leaking solution-only information into user-facing clues.

## Test Plan

- Unit test approved JSONL schema validation.
- Keep one tiny hand-made gold fixture for import/export tests.
- Treat OCR as best-effort; human review correctness is the acceptance gate.
- Test benchmark import creates verifier examples and definition examples from approved records.
- Run targeted verifier tests for current `run_all` misses before promoting prompt changes.
