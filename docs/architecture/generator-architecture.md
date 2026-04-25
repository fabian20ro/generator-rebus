# Generator Architecture — Pseudocode & Randomness Map

Pipeline map: shell entry to publication. Randomness sources, quality gates, component bounds. Reference for impact analysis before modification.

---

## Entry Point 1: `run_all` Supervisor

```
run_all.sh
  └─ run_all.main()
       │
       │  one active slot / topic:
       │    generate, redefine, retitle, simplify
       │
       └─ FOREVER:
            refill empty topic slots
            run non-LLM steps
            batch same-model LLM steps across active topics
            switch model only when current runnable queue empty
            persist completed jobs
```

## Entry Point 2: Retitle Existing Puzzles

```
retitle.main(--date | --puzzle-id | --all-fallbacks)
  │
  │  Fetch puzzle rows from Supabase
  │  ai_client  = create_client()     # LM Studio at localhost:1234
  │  rate_client = create_client()    # separate client for rating
  │
  └─ for each puzzle_row:
       words       = clue.word_normalized from Supabase
       definitions = clue.definition from Supabase
       │
       └─ generate_creative_title(words, definitions, ...)
            │  (same Level 2 loop described below in "Title Generation")
            │  writes new title + title_score + updated_at back to Supabase
```

## Entry Point 3: Redefine Existing Puzzles

```
redefine.main(--date | --puzzle-id | --all | --dry-run | --rounds=7)
  │
  │  Fetch puzzle rows from Supabase
  │  ai_client = create_client()
  │  if multi_model: ensure_model_loaded(PRIMARY_MODEL)
  │
  └─ for each puzzle_row:
       clues = fetch from crossword_clues
       state = build WorkingPuzzle from DB rows
       │
       ├─ verify_working_puzzle(state, client)     # temp=0.0
       ├─ rate_working_puzzle(state, client)        # temp=0.0
       │
       └─ for round in 1..7:                       # --rounds (default 7)
            │  Select candidates: semantic < 7 or rebus < min+1
            │  Skip preset and stuck words
            │  Rewrite each candidate              # temp=0.3
            │  if multi_model: switch model
            │  Re-verify + re-rate changed clues
            │  Update best versions
            │  Plateau detection (lookback=7)
            │
            └─ Restore best versions
               Compare old vs new per clue
               Update crossword_clues.definition in Supabase (unless --dry-run)
```

---

## Batch Publish Pipeline (Single Invocation)

```
batch_publish.run_batch(sizes, seed, rewrite_rounds=30)
  │
  │  raw_words   = load words.json (download if missing)
  │  client      = create_client()          # OpenAI-compat against LM Studio
  │  batch_rng   = Random(seed)             # ← RANDOMNESS: deterministic from seed
  │  ensure_model_loaded(PRIMARY_MODEL)     # default primary: gemma-4
  │
  └─ for each (index, size) in sizes:
```

### Phase 1 — Grid Generation

```
       _prepare_puzzle_for_publication(index, size, ...)
         │
         │  effective_attempts = max(requested, min_preparation_attempts[size])
         │    # size 7→1, 8→1, 9→16, 10→24, 11→32, 12→40
         │    # Why: larger grids have more template variance, so more attempts
         │    # are needed to find one that produces good word quality
         │
         └─ for attempt in 1..effective_attempts:
              │
              │  ── Pick best candidate grid ──
              │  _best_candidate(size, raw_words, rng)
              │    │
              │    │  for each variant in build_relaxed_variants(size):
              │    │    # variant 1: strict settings
              │    │    # variant 2: +1 rarity, 2× backtracks, +2 blacks, +20 budget
              │    │    # variant 3: rarity=5, 3× backtracks, +4 blacks, +35 budget
              │    │    │
              │    │    │  word_index = filter words by max_rarity and max_length
              │    │    │
              │    │    │  ── Template generation ──
              │    │    │  Try incremental template (expensive, once per variant)
              │    │    │    max_blacks = target_blacks + 4  # caps solver calls
              │    │    │    logs only final grid, not per-step
              │    │    │  Fallback: procedural template per attempt
              │    │    │    target_blacks = setting ± rng.choice([-2,-1,0,+1,+2])
              │    │    │                                    # ← RANDOMNESS: black square count
              │    │    │    rejection-sample random black placements
              │    │    │                                    # ← RANDOMNESS: black positions
              │    │    │
              │    │    │  ── Constraint solving (CSP backtracking) ──
              │    │    │  solve(slots, word_index, max_backtracks, rng)
              │    │    │    MRV heuristic + forward checking
              │    │    │    rng.shuffle(candidates) per slot   # ← RANDOMNESS: word selection
              │    │    │    max_backtracks: 80K (7×7) → 500K (12×12)
              │    │    │
              │    │    │  score_words() → QualityReport
              │    │    │    penalizes: 2-letter words, high rarity, low diversity
              │    │    │    returns first solved candidate (greedy, not exhaustive)
              │    │    │
              │    │    └─ return best Candidate by score
              │    │
              │    └─ raises if no variant produced a solution
```

### Phase 2 — Definition Generation

```
              │  puzzle = parse_markdown(candidate.markdown)
              │  generate_definitions_for_puzzle(puzzle, client)
              │    │
              │    │  for each clue:
              │    │    if word in PRESET_DEFINITIONS → use preset, skip LLM
              │    │    else:
              │    │      generate_definition(client, word, theme, retries=3)
              │    │        temperature = 0.2           # low: factual definitions
              │    │        max_tokens  = 160
              │    │        retries     = 3             # ← RANDOMNESS: 3 independent LLM samples
              │    │        pick first non-empty response
```

### Phase 3 — Rewrite Loop (Quality Core)

```
              │  state = working_puzzle_from_puzzle(puzzle)
              │  _rewrite_failed_clues(state, client, rounds=30, multi_model=True)
              │    │
              │    │  ── Initial evaluation ──
              │    │  if multi_model: load SECONDARY_MODEL (eurollm-22b)
              │    │  verify_working_puzzle(state, client)       # temp=0.0
              │    │  rate_working_puzzle(state, client)         # temp=0.0
              │    │  update_best_clue_version() for all clues
              │    │
              │    │  min_rebus_history = []
              │    │
              │    └─ for round in 1..30:                       # MAX_REWRITE_ROUNDS
              │         │
              │         │  ── Plateau detection ──
              │         │  Track min(rebus_score) across non-preset clues
              │         │  if has_plateaued(history, lookback=7):   # PLATEAU_LOOKBACK
              │         │    break  # no improvement in 7 rounds → stop
              │         │
              │         │  ── Select candidates for rewrite ──
              │         │  A clue needs rewrite when:
              │         │    - no definition or placeholder "[...]"
              │         │    - semantic_exactness < 7            # RATE_MIN_SEMANTIC
              │         │    - rebus_score < (current_min + 1)  # progressive bar
              │         │    - NOT locked (semantic ≥ 9 AND rebus ≥ 8)
              │         │    - NOT in stuck_words set
              │         │    - NOT in PRESET_DEFINITIONS
              │         │
              │         │  if no candidates → break (all clues acceptable)
              │         │
              │         │  ── Rewrite each candidate ──
              │         │  for each candidate clue:
              │         │    if placeholder → generate_definition()   temp=0.2
              │         │    else → rewrite_definition()              temp=0.3
              │         │      includes: wrong_guess, rating_feedback,
              │         │                bad_example (from round ≥ 2)
              │         │                                    # ← RANDOMNESS: LLM sampling
              │         │
              │         │  ── Switch model & re-evaluate ──
              │         │  if multi_model: switch to other model
              │         │    primary ↔ secondary            # alternates each round
              │         │  verify changed clues only          temp=0.0
              │         │  rate changed clues only            temp=0.0
              │         │
              │         │  ── Version selection ──
              │         │  for each changed clue:
              │         │    compare current vs. best using deterministic rank
              │         │    if tied → LLM tiebreaker         temp=0.0
              │         │    if semantic ≥ 9 AND rebus ≥ 8 → lock clue
              │         │
              │         │  ── Stuck detection ──
              │         │  if same clue fails 5× consecutively → stuck_words
              │         │                                    # MAX_CONSECUTIVE_FAILURES
              │         │
              │         └─ after loop: restore all clues to their best version
```

### Phase 4 — Title Generation

```
              │  generate_title_for_final_puzzle(puzzle, client, rate_client, multi_model)
              │    │
              │    │  words       = all unique words from clues
              │    │  definitions = all non-placeholder definitions
              │    │
              │    └─ generate_creative_title(words, definitions, ...)     # Level 2
              │         │
              │         │  if no words → return random.choice(FALLBACK_TITLES)
              │         │                                    # ← RANDOMNESS: fallback pick
              │         │
              │         └─ for round in 1..7:               # MAX_TITLE_ROUNDS
              │              │
              │              │  ── Generate one title ──    # Level 1
              │              │  _generate_single_title(definitions, client)
              │              │    if definitions → definitions-only prompt
              │              │    elif words     → words-only prompt (fallback)
              │              │    temperature = 0.9          # high: creative titles
              │              │    max_tokens  = 50
              │              │                              # ← RANDOMNESS: LLM sampling (high temp)
              │              │
              │              │  ── Sanitize ──
              │              │  strip quotes, keep max 5 words
              │              │  reject if: ≥2 commas, blocked words, ALL CAPS
              │              │  reject if: any puzzle word of length ≥3 appears in title
              │              │  reject if: already rejected or is a fallback title
              │              │
              │              │  ── Switch model ──
              │              │  _try_switch_model() if multi_model
              │              │
              │              │  ── Rate creativity ──
              │              │  rate_title_creativity(title, words, rate_client)
              │              │    temperature = 0.1          # near-deterministic rating
              │              │    returns JSON {creativity_score: 1-10, feedback: "..."}
              │              │
              │              │  Track best_title (highest score seen)
              │              │
              │              │  if score ≥ 8 → accept immediately
              │              │                              # TITLE_MIN_CREATIVITY
              │              │  else → add to rejected list (shown in next round's prompt)
              │              │
              │              │  ── Switch model again ──
              │              │  _try_switch_model() if multi_model
              │              │
              │              └─ after 7 rounds: return best_title or random fallback
```

### Phase 5 — Publication

```
              │  ── Compare puzzle attempts ──
              │  _better_prepared_puzzle(best, candidate)
              │    prefer publishable over non-publishable
              │    if score delta > 0.25 → pick higher    # PUZZLE_TIEBREAK_DELTA
              │    else → LLM tiebreaker                  temp=0.0
              │
              │  if non-publishable after all attempts → RuntimeError
              │
              │  ── Upload ──
              │  difficulty = star rating from rarity (1-5 stars)
              │  upload_puzzle() → Supabase
              │  set_published(puzzle_id, True)
              │  write template.md, filled.md, defs.md, verified.md
              │  collect word_metrics, puzzle_metrics
              │
              └─ write manifest.json + metrics.json
```

---

## Randomness Inventory

| Source | Deterministic? | Controlled by | Temperature / Range |
|--------|---------------|---------------|---------------------|
| Batch seed | Yes (per batch) | `--seed` or `SystemRandom` | 1..10M |
| Template black count | Yes (from batch_rng) | `rng.choice([-2..+2])` around target | target ± 2 |
| Template black placement | Yes (from batch_rng) | rejection sampling | — |
| CSP word selection | Yes (from batch_rng) | `rng.shuffle(candidates)` | — |
| Definition generation | **No** | LLM sampling | temp=0.2, retries=3 |
| Definition rewrite | **No** | LLM sampling | temp=0.3 |
| Verification | Near-deterministic | LLM | temp=0.0 |
| Rating (definitions) | Near-deterministic | LLM | temp=0.0 |
| Tiebreaker (clue/puzzle) | Near-deterministic | LLM | temp=0.0 |
| Title generation | **No** | LLM sampling | temp=0.9 |
| Title rating | Near-deterministic | LLM | temp=0.1 |
| Fallback title | **No** | `random.choice()` | 20-item pool |
| Model switching | Deterministic | round parity | A→B→A→B... |

---

## Temperature Map (all LLM calls)

| Call | Module | Temperature | Why |
|------|--------|-------------|-----|
| `generate_definition` | ai_clues | 0.2 | Factual, low variance |
| `rewrite_definition` | ai_clues | 0.3 | Slightly more creative rewrites |
| `verify_definition` | ai_clues | 0.0 | Deterministic guess |
| `rate_definition` | ai_clues | 0.0 | Consistent scoring |
| `choose_better_clue_variant` | ai_clues | 0.0 | Deterministic tiebreak |
| `choose_better_puzzle_variant` | ai_clues | 0.0 | Deterministic tiebreak |
| `_generate_single_title` | theme | 0.9 | Maximum creativity |
| `rate_title_creativity` | theme | 0.1 | Near-deterministic rating |

---

## Quality Gate Thresholds

| Constant | Value | Location | Meaning |
|----------|-------|----------|---------|
| `RATE_MIN_SEMANTIC` | 7 | ai_clues | Minimum semantic exactness to keep a definition |
| `RATE_MIN_REBUS` | 5 | ai_clues | Minimum rebus score (guessability) |
| `LOCKED_SEMANTIC` | 9 | batch_publish | Clue is locked (no more rewrites) if semantic ≥ 9 |
| `LOCKED_REBUS` | 8 | batch_publish | ...AND rebus ≥ 8 |
| `TITLE_MIN_CREATIVITY` | 8 | theme | Accept title immediately if creativity ≥ 8 |
| `MAX_TITLE_ROUNDS` | 7 | theme | Max title generation attempts |
| `MAX_REWRITE_ROUNDS` | 30 | batch_publish | Max definition rewrite rounds |
| `PLATEAU_LOOKBACK` | 7 | batch_publish | Rounds without improvement → stop |
| `MAX_CONSECUTIVE_FAILURES` | 5 | batch_publish | Same clue failing → mark stuck |
| `PUZZLE_TIEBREAK_DELTA` | 0.25 | batch_publish | Score gap needed to skip LLM tiebreak |
| `REDEFINE_ROUNDS` | 7 | redefine | Default rewrite rounds for definition improvement |

---

## Non-Obvious Design Choices

### Dual-Model Alternation Rationale
Cross-validation. High scores across models ensure quality, avoid single-model bias. Default: gemma-4 + eurollm-22b (config: `packages/rebus-generator/src/rebus_generator/platform/llm/models.py`). Writer (temp 0.2-0.3) vs. Scorer/Verifier (temp 0.0). Catch hallucinations + self-rating inflation. Round-based alternation (not per clue) amortizes LM Studio switch overhead (~5-15s).

### Title Temp 0.9 vs. Rating Temp 0.1
Creativity vs. Stability. High temp ensures round-to-round diversity, avoids "safe" convergence. Low temp ensures rating consistency for reliable accept/reject loop. Split maximizes exploration while maintaining quality gate.

### Progressive Rewrite Bar (`current_min + 1`)
Floor-lifting dynamic. Initial focus: baseline (rebus ≥ 5). Bar rises as weakest clues improve. Convergence on uniform quality, prevents over-polishing good clues while ignoring weak ones. Plateau detector (7 rounds) halts loops on difficult words.

### First Solved Candidate Strategy
Cost optimization. CSP expensive (500K backtracks for 12×12). `build_relaxed_variants` cascade handles constraints. Rarity-filtered index ensures first solution adequacy. `preparation-attempts` loop (1-40) provides diversity. Full define→rewrite→title pipeline makes definition quality primary.

### Random Fallback Titles
Pattern avoidance. Deterministic hashing (e.g. on word lists) causes repetitive titles. `random.choice` from 20-title pool ensures even distribution across batches.

### Definition (0.2) vs. Rewrite (0.3) Temps
Precision vs. Iteration. First-pass: factual precision. Rewrites: feedback-guided creativity ("wrong answer X", "vague"). Higher temp escapes local minima; feedback constrains search space.

### Incremental `max_blacks` Cap (`target_blacks + 4`)
Solver call reduction. Capping prevents excessive expensive CSP calls. Aligns with most relaxed variant ceiling. Covers all procedural randomization (±2) and variant offsets (+2/+4).

### `min_solver_step` Optimization
Avoidance of guaranteed failures. Sparse black placement (early steps) creates unfillable slots. 12×12 (effective_max=24) fails steps 1-17. `min_solver_step = max(1, effective_max - 6)` restricts solver to final ~7 steps. Eliminates ~16 wasted calls (500K backtracks each).

### `probe_backtracks` (1/3 max)
Solvability probing. Incremental phase only checks feasibility. High-backtrack templates produce poor word selection. `max_backtracks // 3` (~166K for 12×12) saves 3× time. Full budget reserved for final `_generate_candidate`.

### Lazy Candidate Evaluation
Avoidance of redundant scans. Lazy evaluation replaces full-list shuffle ([0] pick). Distribution identical. Skips `_is_connected` BFS and `_creates_single_letter` scans on ~90% of unused cells.

### Title Rejection Threshold (2+ Word Match)
Balance creativity vs. quality. Single word match (e.g. "Sub Munte") allows evocative riffing. Dual match (e.g. "Munte și Plimbare") deemed "lazy list". Only length ≥ 4 words count; prevents false positives on function words (e.g. "ZI", "FOC").
