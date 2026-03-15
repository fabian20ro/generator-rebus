# Generator Architecture — Pseudocode & Randomness Map

This document traces the full pipeline from shell entry point to published puzzle,
annotating every source of randomness, every quality gate, and the bounds each
component oscillates within. Use it to understand *where* a change will have
effect before proposing code modifications.

---

## Entry Point 1: Batch Generation Loop

```
run_batch_loop.sh
  └─ loop_controller.main()
       │
       │  sizes = [7, 8, 9, 10, 11, 12]          # OVERNIGHT_LOOP_SIZES
       │
       └─ FOREVER:
            for each size in sizes:
              seed = SystemRandom.randint(1..10_000_000)    # ← RANDOMNESS: OS entropy
              subprocess → batch_publish.main(--size, --seed, --rewrite-rounds=30)
            sleep 2s
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
            │  writes new title back to Supabase
```

---

## Batch Publish Pipeline (one invocation)

```
batch_publish.run_batch(sizes, seed, rewrite_rounds=30)
  │
  │  raw_words   = load words.json (download if missing)
  │  client      = create_client()          # OpenAI-compat against LM Studio
  │  batch_rng   = Random(seed)             # ← RANDOMNESS: deterministic from seed
  │  ensure_model_loaded(PRIMARY_MODEL)     # gpt-oss-20b
  │
  └─ for each (index, size) in sizes:
```

### Phase 1 — Grid Generation

```
       _prepare_puzzle_for_publication(index, size, ...)
         │
         │  effective_attempts = max(requested, min_preparation_attempts[size])
         │    # size 7→1, 8→1, 9→16, 10→24, 11→32, 12→40
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

### Phase 3 — Rewrite Loop (the heart of quality)

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
              │         │    gpt-oss-20b ↔ eurollm-22b      # alternates each round
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
              │              │  strip quotes, truncate to 4 words
              │              │  reject if: ≥2 commas, blocked words, ≥2 puzzle words in title
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
              │              │  if score ≥ 5 → accept immediately
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
| `TITLE_MIN_CREATIVITY` | 5 | theme | Accept title immediately if creativity ≥ 5 |
| `MAX_TITLE_ROUNDS` | 7 | theme | Max title generation attempts |
| `MAX_REWRITE_ROUNDS` | 30 | batch_publish | Max definition rewrite rounds |
| `PLATEAU_LOOKBACK` | 7 | batch_publish | Rounds without improvement → stop |
| `MAX_CONSECUTIVE_FAILURES` | 5 | batch_publish | Same clue failing → mark stuck |
| `PUZZLE_TIEBREAK_DELTA` | 0.25 | batch_publish | Score gap needed to skip LLM tiebreak |

---

## Non-Obvious Design Choices

### Why two models alternate instead of using a single better one

Cross-validation. A definition that scores well on gpt-oss-20b *and* eurollm-22b is
more likely to be genuinely good than one that only satisfies a single model's biases.
The writing model generates at temp=0.2-0.3, then the *other* model verifies at temp=0.0
and rates at temp=0.0. This catches model-specific hallucinations and inflated
self-ratings. The alternation happens per rewrite round (not per clue) to amortize the
expensive model load/unload via LM Studio REST API (~5-15 seconds per switch).

### Why title generation uses temp=0.9 but rating uses temp=0.1

Titles need genuine creativity — the same prompt should produce diverse candidates across
rounds, not converge on a single "safe" answer. The rating, however, must be stable:
if the same title is rated twice it should get a similar score, otherwise the
accept/reject loop would be unreliable. The 0.9/0.1 split maximizes exploration in
generation while keeping the quality gate consistent.

### Why the rewrite bar is progressive (`current_min + 1`)

Early rounds focus on getting *any* acceptable definition (rebus ≥ 5). Once the weakest
clue improves, the bar rises. This creates a "lift the floor" dynamic where the rewrite
loop converges on uniform quality rather than perfecting already-good clues while
ignoring weak ones. The plateau detector (7 rounds without improvement) prevents
infinite loops when a word is genuinely hard to define.

### Why grid generation returns the first solved candidate (not the best of N)

Constraint solving is expensive (up to 500K backtracks for 12×12). The quality scoring
(`score_words`) runs on the solved grid and the `build_relaxed_variants` cascade
ensures progressively looser constraints. Within a variant, the first solved grid is
usually good enough because the word index is already filtered by rarity. The
preparation-attempts loop at a higher level (1-40 attempts depending on size) provides
the grid diversity, while each attempt goes through the full define→rewrite→title
pipeline — so grid quality is less important than definition quality.

### Why `_fallback_title()` uses `random.choice` instead of a deterministic hash

Deterministic fallbacks (e.g. hashing the word list) would mean the same puzzle always
gets the same fallback title, creating visible patterns in the published set. Random
selection from the 20-title pool means fallbacks are evenly distributed even when
multiple puzzles in the same batch hit the fallback path.

### Why definitions use temp=0.2 but rewrites use temp=0.3

First-pass definitions should be factually precise — the LLM is defining a Romanian word
from scratch. Rewrites are guided by specific feedback ("leads to wrong answer X",
"too vague") so the model has more context to be creative with. The slightly higher
temperature gives rewrites room to escape the local minimum of the previous definition
while the feedback constrains the search space.

### Why `_sanitize_title` rejects at 2+ matching puzzle words, not 1

A title like "Sub Munte" (containing one puzzle word) can be evocative and thematic.
But "Munte și Plimbare" (two puzzle words) is just a word list disguised as a title.
The threshold of 2 balances creativity (allowing the LLM to riff on a word) against
quality (rejecting lazy concatenations). Only words of length ≥ 4 count, so short
function words like "ZI" or "FOC" don't trigger false rejections.
