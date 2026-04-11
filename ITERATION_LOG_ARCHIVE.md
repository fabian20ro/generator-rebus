# Iteration Log Archive

> append-only. older entries from ITERATION_LOG.md.

### [2026-03-30] Persist rewrite structural rejection reasons, auto-balance overnight sizes, and centralize Supabase update logs

**Context:** user wanted failed rewrite attempts to remember exact structural rejection causes across rounds, `run_batch_loop.sh` to stop looping blindly through `7..15`, and Supabase updates to emit a generic centralized log message.
**Happened:** Added `RewriteAttemptResult` in `generator/core/ai_clues.py` and kept `rewrite_definition()` backward-compatible by returning a string by default while exposing `return_diagnostics=True` for the rewrite engine. Extended `ClueAssessment` with `rewrite_rejection_reason`, updated `generator/core/rewrite_engine.py` to persist the last structural rejection only when rewrite produced no usable candidate, and updated `_synthesize_failure_reason()` to prefer verify/rating signals before falling back to that structural reason. Added `generator/core/supabase_ops.py` with shared `execute_logged_update(...)` and switched current Supabase update sites in `activate`, `redefine`, `repair_puzzles`, and `retitle` to use it. Extended `generator.loop_controller` with `--auto-size`, live counting of `crossword_puzzles.grid_size`, missing-size-as-zero balancing, and smallest-size tie-break; updated `run_batch_loop.sh` to launch loop controller with `--auto-size`.
**Verification:** `python3 -m pytest tests/test_loop_controller.py tests/test_ai_clues.py tests/test_rewrite_engine.py tests/test_batch_publish.py -q` (`120 passed in 0.71s`); `python3 -m py_compile generator/core/ai_clues.py generator/core/pipeline_state.py generator/core/rewrite_engine.py generator/core/score_helpers.py generator/core/supabase_ops.py generator/loop_controller.py generator/phases/activate.py generator/redefine.py generator/repair_puzzles.py generator/retitle.py`
**Outcome:** success
**Insight:** rewrite structural failures need their own persisted channel distinct from verify/rate failure reasons, and overnight size balancing belongs in the Python controller against live Supabase inventory rather than in a blind shell size loop.
**Promoted:** yes

---

### [2026-03-26] Make both title models generate per round and share text cleanup with clue generation

**Context:** user observed that `gpt-oss-20b` still often returned empty title content or weak short-title behavior even after increasing token budget, while definition generation remained stable. They wanted title generation to behave more like definition generation: lower temperature, shared output cleanup, and both models allowed to generate candidates instead of only using the secondary model as an empty-response fallback.
**Happened:** Refactored `generator/phases/theme.py` so each title round now queries both generators when `multi_model=True`, rates each candidate cross-model, and keeps the best valid result across both models/rounds. Lowered title-generation temperature from `0.9` to `0.3`. Extracted the plain-text cleanup logic from `generator/core/ai_clues.py` into shared helper `generator/core/llm_text.py`, then reused it for titles so model output now strips wrappers/labels/markdown-like noise before title validation, just like definition generation does. Updated theme tests for the new per-round dual-generator flow and reran title/batch/retitle/repair plus clue-generation coverage.
**Verification:** `python3 -m pytest tests/test_text_rules.py tests/test_theme.py tests/test_retitle.py tests/test_repair_puzzles.py tests/test_batch_publish.py tests/test_ai_clues.py -q` (`148 passed in 0.47s`)
**Outcome:** success
**Insight:** short creative-title generation should not use a fundamentally different output-cleaning contract from definition generation; shared cleanup and parallel model generation reduce both empty-output and formatting-noise failure modes.
**Promoted:** no

---

### [2026-03-26] Raise title-generation token budget to avoid gpt-oss reasoning-only empty outputs

**Context:** user reported `run_title_improve.sh` getting repeated `"(gol)" -> titlu gol` rounds for many puzzles. Investigation showed `gpt-oss-20b` was often filling LM Studio's `reasoning` field but leaving `message.content` empty on the title prompt, so the pipeline interpreted the result as an empty title.
**Happened:** Reproduced the raw chat-completions call locally and confirmed the model frequently returned empty `content` at the old `max_tokens=50` title budget. Increased title-generation `max_tokens` in `generator/phases/theme.py` to `500`, matching the user's requested safer budget, and synchronized the title prompts (`generator/prompts/system/theme.md`, `generator/prompts/user/title_generate.md`) from `2-4` to `2-5` words so prompt text matches the current validator rules.
**Verification:** `python3 -m pytest tests/test_theme.py tests/test_retitle.py tests/test_batch_publish.py tests/test_repair_puzzles.py -q` (`88 passed in 0.53s`)
**Outcome:** success
**Insight:** local reasoning models can exhaust a tiny completion budget on hidden reasoning and emit blank final text; short-form generation paths need a larger token budget than the apparent final answer length suggests.
**Promoted:** no

---

### [2026-03-26] Force deterministic `Fara titlu` when title generation never beats score 0

**Context:** after the title-score refactor, user wanted the all-failure case to stop producing random fallback labels; if the maximum title score after all 7 rounds stays `0`, the result should be deterministic.
**Happened:** Updated `generator/phases/theme.py` so the no-signal outcome now returns `Fara titlu` with `score=0` and `used_fallback=True` instead of a random fallback-pool label. This covers both paths: all 7 rounds invalid during candidate review, and valid candidates whose best creativity score never rises above `0`. Added theme tests for both scenarios and reran the title-related suites to confirm batch/retitle/repair behavior stays intact.
**Verification:** `python3 -m pytest tests/test_theme.py tests/test_retitle.py tests/test_repair_puzzles.py tests/test_batch_publish.py -q` (`88 passed in 0.60s`)
**Outcome:** success
**Insight:** deterministic failure labels beat random fallback titles when the generator produced no usable signal at all; otherwise repeated maintenance runs look noisy even though nothing improved.
**Promoted:** no

---

### [2026-03-25] Add DEX-driven usage-label suffixes to clue prompts and clue text

**Context:** user wanted clues for marked senses to carry one explicit usage/register suffix like `(arh.)`, `(inv.)`, `(tehn.)`, `(reg.)` so solvers are warned when the intended answer is rarer or domain-specific. Requirement: source labels only from explicit text already present in `dex_definitions`; apply consistently to define/verify/rewrite/rate; and bias rating so justified suffixes help rare words while gratuitous suffixes hurt common words.
**Happened:** Extended `generator/core/ai_clues.py` with explicit DEX-text label extraction, fixed precedence across supported suffixes, trailing-suffix stripping/normalization, and prompt-context builders for generate/rewrite/verify/rate. `generate_definition()` and `rewrite_definition()` now normalize outputs to at most one supported trailing suffix and remove gratuitous suffixes when DEX gives no explicit support; validation ignores the trailing label so one-word gloss checks and other guards still apply to the semantic core of the clue. Updated prompt templates and system prompts so generate/rewrite use the suffix when DEX supports it, verify treats a trailing suffix as real sense/register guidance, and rate scores suffixes asymmetrically: helpful for rare/specialized disambiguation, harmful when gratuitous on common words. Added explicit examples in prompt files where those prompts already had examples. Expanded `tests/test_ai_clues.py` with extraction, prompt, normalization, and prompt-example assertions, and updated `tests/test_verify.py` to assert suffixed definitions are passed intact. Also hardened two verify-phase tests by mocking `LmRuntime.activate_primary()` so unit coverage no longer depends on live LM Studio state.
**Verification:** `python3 -m pytest tests/test_ai_clues.py tests/test_verify.py tests/test_rewrite_engine.py -q`; `python3 -m pytest tests/test_redefine.py -q`.
**Outcome:** success
**Insight:** once clue text gains machine-added parenthetical suffixes, clue validation must strip them before word-count and dangling-ending checks; otherwise the suffix itself can mask bad one-word glosses.
**Promoted:** no

---

### [2026-03-25] Refresh redefine metadata/clue state after each persisted clue update

**Context:** user wanted `run_definition_improve.sh` / `generator.redefine` to stop updating only `crossword_clues.definition`. New requirement: after each persisted clue delta, also refresh puzzle-level Supabase metadata, persist clue `verify_note` + `verified`, keep title fixed, and backfill missing metadata even when no clue row changes.
**Happened:** Refactored `generator/redefine.py` into a two-state flow: baseline puzzle is re-evaluated from DB rows first, then a separate candidate puzzle runs the rewrite loop. `fetch_clues()` now loads `clue_number`, `verify_note`, and `verified`; `build_working_puzzle()` imports existing verify state instead of discarding it. Persist path now compares stable clue keys `(direction,start_row,start_col)` and writes full clue payloads `{definition, verify_note, verified}` one row at a time. After each clue write, an in-memory persistence puzzle is advanced to that clue’s final version, puzzle assessment is recomputed via shared `puzzle_metrics` helpers, and `crossword_puzzles` gets refreshed `description`, numeric scores/counts, `pass_rate`, plus `updated_at` and `repaired_at`. Added no-op/backfill handling for the zero-clue-delta cases. Expanded `tests/test_redefine.py` to cover imported verify state, clue-state persistence, per-clue metadata refresh, backfill-only behavior, no-op when metadata already exists, and state-only deltas.
**Verification:** `python3 -m pytest tests/test_redefine.py`; `python3 -m pytest tests/test_repair_puzzles.py`.
**Outcome:** success
**Insight:** redefine/repair flows should key DB clue persistence by stable coordinates, not `word_normalized`; duplicate answers are legal, while `(direction,start_row,start_col)` is the actual persistence contract.
**Promoted:** no

---

### [2026-03-24] Implement normalized-only Rust engine and pinned Python variant hydration

**Context:** user wanted the Rust phase-1 engine to own only normalized grid fill, dedupe input by normalized word before search, forbid duplicate normalized answers in solved grids, use minimum rarity across variants for normalized-word quality, and move concrete variant resolution (`word_original`, `word_type`, later metadata) fully outside Rust while keeping that choice fixed once selected.
**Happened:** Refactored `crossword_engine` into a normalized-answer engine: `words.rs` now groups source rows by normalized key, dedupes before indexing, aggregates min rarity per normalized word, and drops original-word ownership from `WordEntry`; `quality.rs` now computes rarity-aware definability from the normalized key + min rarity and reports nonzero rarity metrics; `engine.rs` now returns explicit `EngineError`s instead of panicking on invalid size/input, removes answer reuse for all sizes, switches from the old relaxed-variant search to a monotonic black-dot ladder, stops after the first valid solution via cooperative cancellation, emits normalized words only, and adds dictionary/relaxation status stats; `solver.rs` now threads cancellation through recursion and removes runtime `expect` assumptions. On the Python side, `generator/batch_publish.py` now groups metadata rows by normalized word, randomly chooses one concrete variant once per puzzle clue after Rust fill hydration, rewrites clue originals from that pinned choice, and injects pinned `word_type`/`word_original` into the later state so define/verify/rewrite/title use a stable variant instead of last-row-wins metadata lookups. Updated Rust CLI tests and batch tests for the new output/metadata behavior.
**Verification:** `cargo test --manifest-path crossword_engine/Cargo.toml`; `python3 -m pytest tests/test_batch_publish.py -q`; `python3 -m pytest -q`; direct smoke run `cargo run --quiet --manifest-path crossword_engine/Cargo.toml --bin crossword_phase1 -- --size 7 --words generator/output/words.json --seed 42 --preparation-attempts 1`.
**Outcome:** success
**Insight:** normalized-only fill is only stable if the later pipeline pins one concrete variant per clue immediately after hydration; otherwise metadata randomization leaks into downstream behavior.
**Promoted:** yes — see LESSONS_LEARNED entry on pinning one concrete variant per clue after normalized-only fill.

---

### [2026-03-23] Repair regressions after Python phase-1 removal

**Context:** after deleting the legacy Python phase-1 stack, full CI still failed in `test_ai_clues` and `test_verify`, and an old manual size sweep had left native `crossword_phase1` processes running in the terminal.
**Happened:** Restored the shared `ENGLISH_HOMOGRAPH_HINTS` mapping in `generator/core/quality.py` with the Romanian senses already documented in the definition system prompt, so `_build_generate_prompt()` again emits the expected anti-English warning for words like `AN`. Updated `generator/phases/verify.py` to pass `model=` only when a concrete model name exists, preserving the old mock-call contract when `model_name is None`. Stopped the stale `crossword_phase1` background processes from the earlier 13/14/15 sweep.
**Verification:** `python3 -m pytest tests/test_ai_clues.py tests/test_verify.py -q` (`49 passed`); `python3 -m pytest -q` (`369 passed`)
**Outcome:** success
**Insight:** deleting “unused” helpers is safe only after a repo-wide search for non-phase-1 consumers; prompt helpers and tests still depended on the homograph-hint table.
**Promoted:** no

---

### [2026-03-23] Remove legacy Python phase-1 generator path after Rust migration

**Context:** user explicitly wanted the old Python phase-1 crossword-generation code gone after the Rust engine takeover; no dead fallback, no standalone Python template/fill commands left around.
**Happened:** Removed the Python phase-1 implementation from `generator/batch_publish.py` (`_build_index`, `_generate_candidate`, `_best_candidate_python`, template-generation hooks, solver/index imports) and made `_best_candidate(...)` Rust-only with required `words_path`. Deleted obsolete Python modules `generator/core/constraint_solver.py`, `generator/core/grid_template.py`, `generator/core/word_index.py`, `generator/phases/generate_grid.py`, and `generator/phases/fill.py`. Simplified `generator/core/size_tuning.py` down to size lists plus batch retry floors, since the old Python-specific backtrack/rarity/template settings were no longer used. Removed `generate-grid`/`fill` from `generator/rebus.py`, updated `scripts/benchmark_phase1.py` to benchmark Rust only, and trimmed tests accordingly by deleting `tests/test_constraint_solver.py`, `tests/test_grid_template.py`, and `tests/test_quality.py` plus removing Python-phase-1-specific assertions from `tests/test_batch_publish.py`. Restored a minimal shared `ENGLISH_HOMOGRAPH_HINTS` constant in `generator/core/quality.py` after test collection revealed `ai_clues.py` still imports it for prompt hints.
**Verification:** `python3 -m py_compile generator/core/quality.py generator/batch_publish.py generator/rebus.py scripts/benchmark_phase1.py generator/core/size_tuning.py generator/core/markdown_io.py tests/test_batch_publish.py tests/test_loop_controller.py`; `python3 -m pytest tests/test_batch_publish.py tests/test_loop_controller.py -q` (`47 passed`); `cargo test --manifest-path crossword_engine/Cargo.toml`
**Outcome:** success
**Insight:** when ripping out a legacy implementation, separate shared lexical helpers from implementation-specific code first; otherwise unrelated runtime imports can fail at test collection.
**Promoted:** no

---

### [2026-03-20] Archive results3 and redesign next 100-experiment campaign

**Context:** user stopped the live 150-experiment campaign after 99 completed runs and wanted a forensic read of what worked, what almost worked, what failed badly, then a fresh 100-experiment plan starting with removals and alternating prompt files.
**Happened:** Analyzed `results.tsv` plus `logs/results_exp150.json`, wrote `build/experiment_reports/results3_campaign_review.md`, archived the finished campaign to `generator/assessment/results3.tsv`, recreated an empty `generator/assessment/results.tsv`, redesigned `scripts/run_experiments.py` to a new 100-experiment single-file campaign with removals first and strong file alternation, tightened git-live result commits to avoid ignored `logs/` paths, and added runner tests for count/ordering.
**Outcome:** success
**Insight:** in live-git campaigns, score history and prompt-state history can diverge; keep prompt backups authoritative and do not assume a “keep” row means the current prompt tree still contains that winning edit
**Promoted:** yes — see LESSONS_LEARNED "Live git experiment commits are not enough to reconstruct winning prompt state"

---

### [2026-03-18] Add grammatical-form checks and richer experiment metadata

**Context:** user wanted future experiments to include prompt-pruning/removal variants, grammatical-form checking, and more readable experiment descriptions/logs.
**Happened:** Updated base prompts and verify pipeline to pass grammatical category into `verify`, added form-agreement instructions to `verify/rate/rewrite/definition`, rewrote pending experiment definitions to include removal-style attempts plus grammatical-form checks, and changed experiment descriptions to include short description + modified file. Backfilled current campaign JSON/TSV descriptions to the richer format. Added unit tests for verify prompt metadata, verify word-type propagation, runner description formatting, and stabilized `test_verify.py` by mocking `DexProvider.for_puzzle()`.
**Outcome:** success
**Insight:** verify/rate unit tests must isolate DEX prefetch or they stop being reliable local tests
**Promoted:** yes — see LESSONS_LEARNED "rate_puzzle() tests must mock DexProvider.for_puzzle()"

---

### [2026-03-18] Recover partial 41-experiment campaign after power loss

**Context:** user lost power during the 100-experiment campaign and wanted exact prompt edits, discarded results in TSV, and smaller logs.
**Happened:** Reconstructed `exp001`-`exp041` change diffs from `scripts/run_experiments.py` plus `logs/march17_campaign.json`; generated markdown/TSV reports under `build/experiment_reports/`. Backfilled discarded experiments into `generator/assessment/multistep_results.tsv`. Split the 3.4 MB monolithic log into `logs/march17_campaign_split/expNNN.log` files. Patched `scripts/run_experiments.py` so future runs write one assessment log per experiment, persist discard rows in TSV, and store `file/find/replace` in the campaign JSON. Confirmed the crash left `generator/prompts/system/definition.md` with `exp042` applied while no `exp042` result existed.
**Outcome:** success
**Insight:** abnormal termination can leave prompt files ahead of recorded experiment state; always diff against the campaign backup after crashes
**Promoted:** yes — see LESSONS_LEARNED entries on interrupted campaigns and per-experiment logs

---

### [2026-03-20] Stop interrupted results run, archive results4, restore best prompt state, add top-k verifier semantics

**Context:** user asked to stop the interrupted `results_exp100` campaign, archive current assessment rows to `results4.tsv`, restore prompt files to the best current experiment backup, then change the verifier so it can emit 2-3 candidate words and count pass if any candidate is correct.
**Happened:** Confirmed the active `tmux` session and compared `generator/prompts/` against `build/prompt_backups/results_exp100_best`. Only one partial experiment edit remained: an added ambiguity-disambiguation line in `generator/prompts/system/definition.md`. Stopped the session, copied `generator/assessment/results.tsv` to `generator/assessment/results4.tsv`, removed the partial line, and rechecked that `generator/prompts/` matched the best-backup tree exactly. Then implemented configurable top-k verification across the pipeline: added `VERIFY_CANDIDATE_COUNT` config, updated verify prompts to request multiple candidates, added response parsing for numbered/comma-separated candidate lists, stored candidate lists in `ClueAssessment`, rendered them into verify notes, propagated “any candidate matches” semantics into `phases/verify.py`, `generator/assessment/run_assessment.py`, `batch_publish.py`, `redefine.py`, `loop_controller.py`, and CLI entrypoints, and extended metrics with stored verify candidates. Added focused tests for prompt formatting, multi-candidate parsing, verify success on a non-first correct answer, note roundtrips, and difficulty aggregation of candidate lists.
**Outcome:** success
**Insight:** top-k verification is only useful if notes, metrics, batch publication, and benchmark scoring all adopt the same pass criterion; otherwise “near miss” evidence disappears or contradicts pass-rate metrics
**Promoted:** yes — see LESSONS_LEARNED "Top-k verifier changes need pipeline-wide semantics, not just a prompt tweak"

---

### [2026-03-18] Rebuild multistep benchmark from March 17 and harden runner repeatability

**Context:** user wanted old assessment words replaced with March-17 low/high candidates only; multistep benchmark only; repeatable baseline and full experiment runs.
**Happened:** Aggregated `20260317_*` `metrics.json` files into low/high TSVs with averaged rebus scores. Rewrote dataset builder to produce a 70-word low/medium/high multistep set with short-word caps and reused DEX definitions from the existing dataset. Ran a real baseline on the rebuilt set (`67.8` composite, `28.6%` pass). Patched `run_experiments.py` to stream assessment logs live, restore prompts on interrupt, roll back `multistep_results.tsv` for discarded runs, and support per-campaign log paths / description prefixes. Archived old assessment history and reset active baseline/history to the new March-17 dataset.
**Outcome:** success
**Insight:** append-only assessment artifacts poison hill-climbing unless discarded experiments restore both prompt state and result state
**Promoted:** yes — see LESSONS_LEARNED "Prompt experiment runs must roll back assessment artifacts on discard"

---

### [2026-03-14] Add prefix stripping and forbidden stems to family check

**Context:** TIBETAN burned 8 rewrite rounds because LLM kept using "Tibet". NEINCEPUT-type words not caught by suffix-only family check.
**Happened:** Added Romanian prefix stripping to `clue_family.py`, `forbidden_definition_stems()` function, and `_family_exclusion_note()` in prompt builders. Removed OU/URINARE presets.
**Outcome:** pending observation
**Insight:** prefix stripping + forbidden stems = essential for Romanian morphology in family checks
**Promoted:** yes — see LESSONS_LEARNED "Family check needs prefix stripping"

---

### [2026-03-21] Make rewrite/failure flows use all verifier candidates, not only the first guess

**Context:** user asked whether the change from single-guess verification to top-3 was respected everywhere, including generation and evaluation flows.
**Happened:** Audited the verifier call chain and confirmed that pass/fail, selection, metrics, markdown notes, and multistep assessment already used `verify_candidates` correctly. The remaining gap was rewrite/failure handling: prompts and synthesized failure reasons still mostly used `wrong_guess`, which is just the first failed candidate kept for compatibility. Patched `generator/core/ai_clues.py` so rewrite prompts mention the full verifier output (`Rezolvitorul a propus: ...`) and failure history carries candidate lists, not just one guess. Patched `generator/core/score_helpers.py` so `_synthesize_failure_reason()` prefers the full candidate list. Updated `generator/batch_publish.py` and `generator/redefine.py` to pass `verify_candidates` and richer failure history into rewrite. Added regression tests for prompt rendering and failure synthesis.
**Outcome:** success
**Insight:** once top-k verification exists, `wrong_guess` becomes a lossy compatibility field; decision-making and prompt repair should use `verify_candidates` as the primary signal
**Promoted:** yes — see LESSONS_LEARNED "Top-k verifier support is incomplete if rewrite still sees only the first wrong guess"

---

### [2026-03-21] Align assessment dataset DEX context with live expanded provider context

**Context:** user asked to ensure that the new DEX expansion (`definiție directă` + `sens bază`) reaches all prompts where it should, not only the live generation pipeline.
**Happened:** Traced all DEX call sites. Confirmed that live puzzle generation/rewrite/rating paths already pass `dex.get(...)` into prompt builders, so expanded DEX context reaches `generate`, `rewrite`, and `rate` immediately. Confirmed that `verify` still intentionally does not receive DEX context, to avoid leaking answer-side semantic hints into the guess step. Found one real gap: `generator/assessment/prepare_dataset.py` reused old `dataset.json` `dex_definitions` strings as authoritative, so the multistep benchmark could keep stale pre-expansion DEX text even after the provider improved. Patched `_reuse_or_fetch_dex()` to prefer current provider `lookup()` output from local cache/Supabase before reusing old dataset strings, and added a targeted regression test for stale-vs-live replacement.
**Outcome:** success
**Insight:** when prompt context is pre-materialized into datasets, every improvement in the live context generator needs a refresh path into those cached artifacts or the benchmark silently diverges from production
**Promoted:** yes — see LESSONS_LEARNED "Assessment datasets should refresh DEX text from the live provider, not trust old dataset.json strings forever"

---

### [2026-03-21] Expand DEX semantic-base extraction to short first-definition patterns

**Context:** after reviewing all 540 entries whose first parsed DEX definition has under 10 words, the next task was to extend semantic context beyond pure redirect formulas like `Diminutiv al lui X`.
**Happened:** Added short-first-definition semantic expansion patterns in `generator/core/dex_cache.py` for five approved families: one-word synonym glosses (`Corabie.`), `Acțiunea de a (se) X`, `Faptul de a (se) X`, `Proprietatea de a fi X`, and generalized unit fractions `A <ordinal> parte dintr-un/dintr-o X`. Tightened target cleanup so extracted base lexemes drop trailing punctuation and parenthetical sense markers. Also narrowed expansion triggering to the first parsed DEX definition, which avoids false positives from later examples/citations. Added targeted unit tests for each family plus the generalized `dintr-un/dintr-o` fraction case.
**Outcome:** success
**Insight:** the reliable trigger for this kind of semantic expansion is not “any short definition anywhere in the entry”, but “the first parsed DEX definition is structurally short and points to a base lexeme”
**Promoted:** yes — see LESSONS_LEARNED "Short first-definition DEX patterns are worth semantic expansion when they expose a clear base lexeme"

---

### [2026-03-21] Add gitignored local DEX cache layer before Supabase

**Context:** user wanted the code to stop extracting from Supabase on every run and to use a gitignored local cache folder as part of the normal workflow.
**Happened:** Extended `DexProvider` from a 3-layer cache to a 4-layer cache: memory -> local disk -> Supabase -> dexonline. Added a gitignored default cache directory `.cache/dex_definitions`, with per-word JSON entries storing `status`, `html`, `original`, and `fetched_at`. Wired the local layer into `get()`, `lookup()`, `prefetch()`, redirect dereference lookups, and dexonline fetch storage. Stored negative results locally too (`not_found`) so repeated misses avoid Supabase and HTTP. Added targeted tests for local-disk hit priority, local negative cache, prefetch using local cache, and local persistence after fetch.
**Outcome:** success
**Insight:** local-disk caching has to sit in front of Supabase for both normal lookups and redirect dereference lookups; otherwise the “main” path gets faster but the redirect expansion path still chatters against the remote store
**Promoted:** yes — see LESSONS_LEARNED "DEX cache flow should include a gitignored local disk layer before Supabase"

---

### [2026-03-20] Fix DEX redirect parsing and one-hop semantic expansion

**Context:** `FIRISOR` had a Supabase `dex_definitions` row, but no DEX context reached prompts; the stored HTML contained `Diminutiv al lui <i>fir</i>.`, which both exposed a parser bug and showed that redirect-style definitions are semantically too thin on their own.
**Happened:** Audited `generator/core/dex_cache.py` and `tests/test_dex_cache.py`. Fixed `_DefinitionExtractor` so inline closing tags (`i`, `b`, `em`, etc.) decrement depth correctly instead of leaving `tree-def` spans unclosed. Added redirect/meta-pattern detection for short single-definition entries, with 1-hop dereference to the base lexeme and injection of up to two `Sens bază pentru ...` lines alongside the original DEX definition. Added a separate `uncertain_short_definitions()` collection plus `[DEX short/uncertain] ...` runtime log entries for short unresolved single-definition cases. Added targeted tests for inline markup parsing, `FIRISOR -> fir` expansion, and uncertain short definitions; verified locally that `Diminutiv al lui <i>fir</i>.` now parses and expands as expected.
**Outcome:** success
**Insight:** redirect-style DEX entries fail in two different ways — parser loss and semantic thinness — so the durable fix is parser robustness plus bounded dereference, not either one alone
**Promoted:** yes — see LESSONS_LEARNED "DEX redirect-style definitions need both parser robustness and one-hop expansion"

---

### [2026-03-20] Validate baseline, smoke artifacts, and close lock/publication gaps

**Context:** after the new baseline was recalculated, the next task was runtime validation: confirm the code changes on real artifacts, then continue closing objective-alignment bugs one by one.
**Happened:** Confirmed the new baseline in `generator/assessment/results.tsv` (`c0551f6`, composite `65.0`). Ran multiple real smoke batches against LM Studio under `build/smoke_batch_verify*` and checked `defs.md` plus `metrics.json`. First smoke run exposed three issues: missing `model_generated` provenance on initial clue versions, markdown emphasis leaking into final definitions, and `verified=False` clues escaping blockers because rarity-only override still suppressed rewrites. Fixed those, added tests, reran smoke, then found a second coherence bug: `clue.locked` still depended only on score thresholds, so some `9/8` failures were skipped in rewrite rounds. Fixed lock semantics to require `verified=True`, tightened `_is_publishable()` so blocker-free puzzles still need at least a `0.5` exact-solve pass rate before publication, and hardened `rate_definition()` retries so invalid JSON gets a stricter second prompt instead of the same blind retry. Follow-up smoke runs then exposed structurally weak raw definitions (`Pământ`, `... asupra unei`), an English final title (`Jazz Sunset Echoes`), and leaked rewrite meta-prefixes (`Definiția nouă:`). Added generate/rewrite validation for one-word glosses and dangling endings, Romanian-only title enforcement plus English-title rejection in sanitization, and `_clean_response()` stripping for rewrite meta-prefixes. Instrumented rewrite churn explicitly (`first_passed`, `final_passed`, rewrite attempts/changes/rescues) and corrected the old mislabeled first-pass metric; on the corrected smoke sample, rewrite improved exact solves from `4/22` to `11/22`.
**Outcome:** success
**Insight:** exact-solve alignment has to cover rewrite gating, clue locking, publication thresholds, and the metric plumbing around them — otherwise both the shipped puzzles and the diagnostics lie in different ways
**Promoted:** yes — see LESSONS_LEARNED entries on `locked` semantics, publishable pass-rate floors, and separate first/final pass tracking

---

### [2026-03-20] Fix generator correctness and objective-alignment bugs on main

**Context:** user asked for task lists plus concrete fixes across multiple passes: correctness, objective alignment, metrics, and tests.
**Happened:** Identified and fixed four core issues: `_best_candidate()` returned after the first solved grid; LM Studio model switching unloaded by model key instead of loaded instance id; clean `defs.md` export kept score residue; clue selection and rewrite gating underweighted exact verification. Added richer word-difficulty aggregation fields (`wrong_guess`, `failure_kind`, blocker counts, rebus/guessability averages, rarity-override counts, word type). Added focused tests for model switching, selection ranking, best-candidate search, clean export, and richer metrics. Wrote a pass-based task list under `build/experiment_reports/20260320_generator_task_list.md`.
**Outcome:** success

---

### [2026-03-28] Make prompt autoresearch inspection side-effect free and narrow manifest-anchor coverage

**Context:** definition-improvement audit found two maintenance issues: `prompt_autoresearch.py --dry-run` could rebuild and wipe saved `v3` state on invalid durable state, and the old manifest-anchor test was failing on stale `v1` prompt edits even though current work runs on later experiment sets.
**Happened:** Added side-effect-free inspection flow in `scripts/prompt_autoresearch.py` so `--status` and `--dry-run` read existing state directly and only bootstrap into a temporary directory when no state exists. This bypasses the repair/rebuild path during inspection and keeps durable state untouched. Added a regression test proving dry-run no longer routes through rebuild/run paths. Updated manifest-anchor coverage in `tests/test_run_experiments.py` to validate active/current `v2` and `v3` manifests against live prompt files instead of stale historical `v1` edits. Added a concise manual `v3` runbook to `prompt_research.md`.
**Verification:** `.venv/bin/python -m pytest tests/test_run_experiments.py tests/test_prompt_autoresearch.py tests/test_run_assessment.py tests/test_model_manager.py` (`60 passed`)
**Outcome:** success
**Insight:** inspection commands must never call durable-state repair logic implicitly; once a supervisor owns mutable benchmark state, even “dry-run” paths need a separate read-only codepath.
**Promoted:** no

---

### [2026-03-28] Rotate assessment ledger into results6 and clear working results.tsv

**Context:** user wanted the current `generator/assessment/results.tsv` history preserved before starting a fresh benchmark baseline, and asked whether the current file risked being overwritten.
**Happened:** Copied the full working ledger from `generator/assessment/results.tsv` into new archive file `generator/assessment/results6.tsv`, then reset `results.tsv` back to header-only so the next baseline run starts from a clean working ledger. Left code references pointing at `results.tsv`, since benchmark/runtime paths already target that filename and should keep doing so for the new baseline.
**Outcome:** success
**Insight:** benchmark rotation here is really “archive old ledger, keep canonical filename empty for next run”, because code paths are pinned to `results.tsv` rather than a versioned filename.
**Promoted:** no

---

### [2026-03-28] Prepare rewrite-focused v4 batch and rotate pre-v4 ledger into results7

**Context:** after `v3exp016` beat the freshly rotated baseline and three confirmation runs stayed well above the old score floor, the next request was to stop extending the old `v3` batch, archive the current ledger again before a new official baseline run, and prepare a new `v4` experiment set around the winning rewrite prompt.
**Happened:** Added a new `v4` experiment namespace to `scripts/run_experiments.py`, `scripts/prompt_autoresearch.py`, and benchmark policy constants so tooling now recognizes three rewrite-only families: explicit rule re-additions, header variants, and compactness-bias variants. The `v4` manifest contains eight single-file edits, all targeted at `generator/prompts/system/rewrite.md`, designed to isolate whether the `v3exp016` win came from deleting specific bans, compressing the header, or generally pushing the rewrite prompt toward shorter outputs. Updated prompt-research docs to describe the new lane and the manual `v4` run commands. Archived the just-finished `results.tsv` ledger into `generator/assessment/results7.tsv`, then reset `results.tsv` back to header-only so the next official baseline can land into a clean working ledger.
**Outcome:** success
**Insight:** once a cleanup-style rewrite experiment wins and confirms, the next batch should stop touching unrelated surfaces and instead probe which exact deleted constraints were redundant versus still worth reintroducing one at a time.
**Promoted:** no
**Insight:** selector/rule mismatches between assessment and production create false-positive prompt wins; correctness fixes and objective alignment should land before the next baseline
**Promoted:** yes — see LESSONS_LEARNED entries on selection alignment and LM Studio unload instance ids

---

### [2026-03-22] — Build durable prompt-autoresearch supervisor and reclassify active pilot ledger
**Context:** user wanted the current `uncertain` semantics fixed, a recoverable overnight prompt-improvement loop that does not rely on chat memory or API keys, and the active prompt campaign ledger updated to reflect the stricter interpretation.
**Happened:** Extended `generator/assessment/benchmark_policy.py` with explicit near-miss, research-signal, and family-stopping constants. Refactored `scripts/run_experiments.py` so experiments now carry family/priority/target metadata, word-level gain/loss summaries are computed from assessment JSON, and `classify_experiment_result()` returns a structured decision with `keep / uncertain / discard`, `uncertain_reason`, and reusable research-signal data instead of a loose scalar-only tuple. Added family-outcome summarization for stale-family detection. Added `scripts/__init__.py`, a new durable supervisor at `scripts/prompt_autoresearch.py`, and `prompt_research.md`. The supervisor initializes/loads `build/prompt_research/`, stores incumbent/trial prompt snapshots, replays existing campaign logs, reclassifies log entries against the true baseline JSON, rewrites the active `generator/assessment/results.tsv`, tracks family counters/staleness, and picks the next viable experiment by policy order with bundle-unlock prerequisites. Bootstrapped the durable state from `logs/pilot_20260321.json` and `build/assessment_runs/baseline_results_20260321_20260321_142651.json`; this reclassified the active pilot rows, leaving only `exp001` and `exp002` as keeps and moving the rest to discard. Added tests in `tests/test_run_experiments.py` and new `tests/test_prompt_autoresearch.py` for classifier semantics, family staleness, bundle unlocking, recovery, and campaign replay.
**Outcome:** success
**Insight:** overnight prompt optimization has to be a resumable state machine over prompt snapshots and assessment JSON, not a long chat session or a flat fixed manifest
**Promoted:** yes — see LESSONS_LEARNED entry on externalizing all overnight prompt-research state

---

### [2026-03-21] Add shared runtime logging, shared rewrite engine, and structured assessment artifacts

**Context:** user asked to implement the operational refactor plan end-to-end: one timestamped logging path, one audit flow for unresolved short DEX definitions, less duplicated rewrite logic, assessment aligned with production, and a safer benchmark workflow for prompt experiments.
**Happened:** Added `generator/core/runtime_logging.py` with local human timestamps, UTC persisted timestamps, path-safe run timestamps, stdout/stderr wrapping, and JSONL audit events. Wired it into the main CLI/script entrypoints (`generator.rebus`, `batch_publish`, `redefine`, `retitle`, `run_assessment`, `run_experiments`, `run_multistep_assessment_series`, `loop_controller`) and removed inline timestamp formatting from `model_manager`/DEX fetch output. Extended DEX short-definition handling so unresolved short first definitions emit structured `dex_short_definition_detected` events at detection time and terminal `dex_short_definition_not_included_in_redefinire` events from a new shared `generator/core/rewrite_engine.py`. Added `generator/core/model_session.py` and switched the shared rewrite engine plus multistep assessment to use session-based model orchestration instead of scattered direct `switch_model(...)` calls. Refactored `redefine.py` and `batch_publish.py` to depend on the shared rewrite engine. Extended `generator/assessment/run_assessment.py` to emit a machine-readable JSON artifact with per-tier and protected-control summaries and updated `scripts/run_experiments.py` to consume that artifact, classify experiment outcomes as `keep` / `uncertain` / `discard`, and persist the incumbent assessment summary alongside best prompt backups. Added benchmark policy documentation in `generator/assessment/benchmark_policy.py`. Added tests for timestamped logging, model sessions, rewrite-engine DEX audit emission, and runner uncertainty classification.
**Outcome:** success
**Insight:** the cheapest way to stabilize a fast-moving CLI/LLM pipeline is to centralize runtime concerns first (logging, audit, model/session orchestration), then collapse duplicated control loops onto shared services before touching benchmark policy
**Promoted:** yes — see LESSONS_LEARNED entries on shared process logging and machine-readable assessment artifacts

---

### [2026-03-21] — Curated 20260321 benchmark reset and new 100-experiment manifest
**Context:** user asked to replace the March-style assessment set with the 70 words mined from today’s blind spots, then implement the exact new 100-experiment prompt campaign including late multi-file bundles.
**Happened:** Patched `generator/assessment/prepare_dataset.py` so the default builder now uses a checked-in curated tier map (`30 low / 25 medium / 15 high`) and still refreshes DEX text through the live provider; added tests for exact curated membership and missing-word failure. Regenerated `generator/assessment/dataset.json`, which now contains the requested 70 words exactly. Reworked `scripts/run_experiments.py` to support multi-file experiments with atomic edit application, manifest validation, joined file descriptions, and a repo-root `sys.path` bootstrap so `python3 scripts/run_experiments.py --dry-run` works from the repo root. Replaced the old removal-heavy manifest with the new ordered campaign: 12 cleanup experiments, 24 verify-example refreshes, 12 rewrite anti-distractor edits, 12 definition examples/counterexamples, 12 rate-calibration edits, 12 paired verify bundles, 8 paired definition+rewrite bundles, 4 paired definition+rate bundles, and 4 three-file confirmatory bundles. Added a regression test that asserts every experiment anchor exists in the current prompt files so prompt drift cannot silently turn parts of the campaign into skips.
**Verification:** `python3 -m py_compile generator/assessment/prepare_dataset.py scripts/run_experiments.py`; `python3 -m pytest tests/test_prepare_dataset.py tests/test_run_experiments.py -q` (`11 passed`); `python3 -m generator.assessment.prepare_dataset`; `python3 scripts/run_experiments.py --dry-run`.
**Promoted:** yes — see LESSONS_LEARNED entry on prompt campaign manifests needing anchor-existence tests.

---

### [2026-03-22] — Analyze latest prompt autoresearch block after exp053
**Context:** user asked for concise analysis of the newest autoresearch trials under `build/prompt_research/`, specifically repeated patterns after `exp053`, consistent per-word gains/regressions, and whether the next unrun families still look justified.
**Happened:** Read `LESSONS_LEARNED.md`, then audited `build/prompt_research/current_run.log`, `events.jsonl`, `state.json`, `families.json`, and trial artifacts `exp054`, `exp055`, `exp058`, `exp059`, `exp060`, `exp065`, `exp066`, `exp067`. Counted repeated gain/loss words across the block and compared them to family stale-state and unlock/priority rules in the runner. Found eight straight discards post-`exp053`; three consecutive stale families (`definition_positive_examples`, `definition_guidance`, `rate_rules`) all died on repeated collateral losers. `EPIGASTRU` was the only universal gainer; `ETAN`, `OSTRACA`, and `SAN` regressed in every trial. Immediate next family on resume would be `rewrite_structural_guidance` (`exp040`), while bundle families remain unjustified because all relevant upstream `has_signal` flags are still false.
**Outcome:** success
**Insight:** none promoted; analysis only

---

### [2026-03-21] Implement pilot-first benchmark workflow around baseline_results_20260321

**Context:** user wanted the new March 21 curated benchmark locked in as the working target, the runner limited to a 10-12 experiment pilot first, follow-up block priorities encoded in code, and explicit handling for unstable high-tier controls `ADAPOST` / `ETAN`, with a git commit after each implementation step.
**Happened:** Replaced stale benchmark-policy assumptions with the March 21 reset structure, but after user correction moved incumbent truth back to `generator/assessment/results.tsv` via a `load_latest_kept_result()` helper instead of duplicating baseline metrics in code. Added runner presets for `pilot`, `cleanup`, `verify-examples`, `rewrite-anti-distractor`, `definition-examples`, `rate-exactness-calibration`, and later multi-file bundle blocks, plus `--end-at` for bounded runs while preserving `--dry-run` visibility across all 100 experiments. Added `--summarize-log` so completed logs can classify direction as `verify-led`, `rewrite-led`, `rate-led`, or `noisy / not yet informative`, and return next-preset recommendations following the requested priority order. Added explicit control-word watch logic for `ADAPOST` and `ETAN`, optional `--control-baseline-json` support for comparing against the baseline assessment artifact, and summary output that escalates repeated failures to `demote-or-replace`. Added/updated tests for the benchmark policy helpers, pilot/block selection, direction classification, and control-watch summaries. Created the requested step commits: `2cdbe66`, `48e38c8`, `e08e2b0`, `195b792`.
**Outcome:** success
**Insight:** benchmark policy code should store ranges and decision rules, but incumbent scores belong in the results ledger; per-word stability policy needs assessment JSON, not TSV rows alone
**Promoted:** yes — see LESSONS_LEARNED entry on sourcing benchmark incumbents from `results.tsv`

---

### [2026-03-23] — Move `best_assessment.json` runner cache out of tracked prompt source
**Context:** user asked what `generator/prompts/best_assessment.json` is, whether it should be committed, and, if not, to reimplement things so the artifact lands in an untracked folder.
**Happened:** Traced all references with `rg`. Confirmed only `scripts/run_experiments.py` uses it, via `load_best_result_summary()` / `save_best_result_summary()`, as a cache of the current best assessment summary for the experiment runner. It is not read by generation, assessment, or the prompt autoresearch supervisor. Implemented `best_result_summary_path()` in `scripts/run_experiments.py` so the summary now lives under `build/prompt_experiment_state/<backup_dir_name>/best_assessment.json` instead of inside the prompt snapshot directory. Kept read-only fallback loading from the legacy location (`backup_dir / best_assessment.json`) so older runs still resume cleanly. Added tests asserting the new path is under `build/` and that legacy fallback loading still works. Verified with `python3 -m py_compile scripts/run_experiments.py tests/test_run_experiments.py` and `python3 -m pytest tests/test_run_experiments.py -q` (`24 passed`).
**Outcome:** success
**Insight:** benchmark/cache artifacts should never live beside prompt source files; otherwise they look like source-of-truth and tempt accidental commits.
**Promoted:** yes — see LESSONS_LEARNED entry on benchmark runner artifacts living under gitignored build/state roots

---

### [2026-03-23] — Expand v2 prompt autoresearch pool to ~40 narrow trials and fix rebuild-only side effects
**Context:** user wanted the next v2 batch to run around 40 experiments, not 12, and asked to proceed without git commits. Existing v2 manifest was too small and, with current stale-family thresholds, would stop far earlier than 40. While rebuilding state after expansion, `--rebuild-state` unexpectedly launched a real assessment trial, which violated the intended safe maintenance semantics.
**Happened:** Read `LESSONS_LEARNED.md`, current v2 manifest in `scripts/run_experiments.py`, benchmark policy, and an explorer summary of incumbent weak spots. Reshaped v2 around four narrow hypothesis families aligned to current failure classes: `short_word_exactness`, `near_neighbor_exclusion`, `blank_output_concretization`, and `rare_technical_noun_rescue`. Expanded `scripts/run_experiments.py` from 12 to 40 `v2expNNN` trials, all narrow single-file edits against `system/rewrite.md`, `user/rewrite.md`, or `system/definition.md`; added new v2 presets (`1-40`, plus family ranges), removed bundle unlocks for v2, and added a set-specific campaign-stop helper. Updated `generator/assessment/benchmark_policy.py` to use the new v2 family order and less aggressive thresholds (`V2_CAMPAIGN_STOP_STALE_FAMILIES = 4`, `V2_FAMILY_STOP_* = 10`, `V2_FAMILY_STOP_REPEAT_PRIMARY = 4`) so the next batch can realistically cover about 40 trials. Updated `scripts/prompt_autoresearch.py` to use set-specific stale-family stop limits. While rebuilding state, discovered that `--rebuild-state` still fell through into `run_supervisor()` and launched `v2exp001`; stopped the accidentally spawned `prompt_autoresearch.py` / `run_assessment.py` processes, then fixed `main()` to return immediately after rebuild. Also hardened `generator/core/runtime_logging.py` so `audit()` creates its parent directory lazily, which fixed temp-state supervisor tests. Rebuilt `build/prompt_research_v2/` cleanly after the fix.
**Verification:** `python3 -m py_compile generator/core/runtime_logging.py scripts/run_experiments.py scripts/prompt_autoresearch.py generator/assessment/benchmark_policy.py tests/test_run_experiments.py tests/test_prompt_autoresearch.py`; `python3 -m pytest tests/test_run_experiments.py tests/test_prompt_autoresearch.py tests/test_runtime_logging.py -q` (`38 passed`); `python3 scripts/run_experiments.py --experiment-set v2 --dry-run` (shows `Selected: 40 / 40 experiments`); `python3 scripts/prompt_autoresearch.py --state-dir build/prompt_research_v2 --baseline-json build/prompt_research/incumbent.json --experiment-set v2 --rebuild-state`; `python3 scripts/prompt_autoresearch.py --state-dir build/prompt_research_v2 --status` (valid, incumbent `81.9 / 0.386`, next `v2exp001`, family `short_word_exactness`); `ps -ef | rg 'scripts/prompt_autoresearch.py|generator.assessment.run_assessment'` confirmed no leftover assessment run after the rebuild fix.
**Outcome:** success
**Insight:** maintenance-only autoresearch commands must be side-effect free; if `--rebuild-state` or `--status` can launch a benchmark, the recovery path itself becomes a source of prompt drift and noisy results.
**Promoted:** yes — see LESSONS_LEARNED entry on side-effect-free maintenance commands

---

### [2026-03-23] — Add v3 prompt+system lane, explicit model plumbing, and incumbent-snapshot integrity checks
**Context:** user asked to implement a small mixed `prompt + system` batch instead of another large prompt-only campaign: benchmark integrity cleanup first, then a new v3 experiment lane with four temperature experiments and three narrow prompt families (`verify_minimal_procedural`, `rewrite_generic_exclusion`, `prompt_dedup_cleanup`), plus explicit model plumbing so benchmark behavior no longer depends on LM Studio `default` routing in the main assessment/rewrite paths.
**Happened:** Audited `generator/assessment/run_assessment.py`, `generator/core/ai_clues.py`, `generator/phases/define.py`, `generator/phases/verify.py`, `generator/core/rewrite_engine.py`, `scripts/run_experiments.py`, and `scripts/prompt_autoresearch.py`. Found real breakage during implementation: `scripts/run_experiments.py` added `V3_EXPERIMENTS` but helper `_v2_exp()` still lacked `assessment_overrides` / `scope_label`, so the new system-only lane could not load cleanly. Also found an indentation error in `choose_better_clue_variant()` / `choose_better_puzzle_variant()` and an incumbent-integrity bug in autoresearch rebuilds: after swapping the temp rebuilt state into place, `seed_prompt_snapshot` still pointed at the temp dir and the live `generator/prompts/` tree was not restored from the rebuilt incumbent, so validation reported a false mismatch. Fixed all of that. Added explicit `model` passthrough to `generate_definition()`, `rewrite_definition()`, `verify_definition_candidates()`, `rate_definition()`, and the assessment helpers so pass1 uses `PRIMARY_MODEL.model_id`, pass2 uses `SECONDARY_MODEL.model_id`, and verify/rate cross-checks also receive explicit model ids. Added separate `--generate-temperature` and `--rewrite-temperature` support in assessment and runner plumbing. Added a new `v3` manifest in `scripts/run_experiments.py`: `v3exp001-v3exp004` are system-only temperature trials, `v3exp005-v3exp008` verify minimization edits, `v3exp009-v3exp012` rewrite generic exclusion rules, and `v3exp013-v3exp016` dedup/shortening edits. Updated `prompt_research.md` to match the live v3 family graph and rules. Extended tests: explicit-model passthrough in `tests/test_ai_clues.py`; v3 manifest/overrides in `tests/test_run_experiments.py`; seed/leakage/state validation in `tests/test_prompt_autoresearch.py`; new `tests/test_run_assessment.py` to verify separate generate/rewrite temperatures and explicit model ids in assessment phases. Rebuilt `build/prompt_research_v3/` from `build/prompt_research/incumbent.json` plus `build/prompt_research/snapshots/incumbent_prompts`.
**Verification:** `python3 -m py_compile scripts/run_experiments.py scripts/prompt_autoresearch.py generator/assessment/run_assessment.py generator/core/ai_clues.py generator/phases/define.py generator/phases/verify.py generator/core/rewrite_engine.py tests/test_ai_clues.py tests/test_run_experiments.py tests/test_prompt_autoresearch.py tests/test_run_assessment.py`; `python3 -m pytest tests/test_ai_clues.py tests/test_run_experiments.py tests/test_prompt_autoresearch.py tests/test_run_assessment.py -q` (`82 passed`); `python3 scripts/run_experiments.py --experiment-set v3 --dry-run`; `python3 scripts/prompt_autoresearch.py --state-dir build/prompt_research_v3 --baseline-json build/prompt_research/incumbent.json --seed-prompts-dir build/prompt_research/snapshots/incumbent_prompts --experiment-set v3 --rebuild-state`; `python3 scripts/prompt_autoresearch.py --state-dir build/prompt_research_v3 --status`.
**Outcome:** success
**Insight:** supervisor rebuilds need to restore the live prompt tree and rewrite swapped snapshot paths, not only write fresh JSON; otherwise durability is correct on disk but validation still fails against the wrong live tree.
**Promoted:** yes — see LESSONS_LEARNED entry on refreshing snapshot paths and prompt tree after autoresearch rebuild swap.

---

### [2026-03-22] — Fix mobile rebus scroll-jump and pen-mode clarity
**Context:** user reported that tapping a grid square on phone scrolled the page down toward the clue/definition area, making entry awkward; pen mode was also unclear and its on/off state too implicit.
**Happened:** Audited the frontend rebus flow in `frontend/src/main.ts`, `frontend/src/components/clue-panel.ts`, `frontend/src/components/grid-renderer.ts`, and the toolbar markup/styles. Found the main mobile jump source: every active-clue update called `scrollIntoView()` on the active clue item, which on stacked mobile layout scrolled the whole page to the clue list below the grid. Fixed that by auto-scrolling only when `.clues-container` is itself scrollable. Hardened cell focus with `focus({ preventScroll: true })` fallback to plain `focus()`. Replaced the icon-only pencil button with explicit `Creion` + `Pornit/Oprit` state, added `aria-pressed`, a distinct off-state color, and centralized button rendering in `main.ts`. Added `frontend/src/components/pencil-help.ts` with a one-time modal explainer persisted via `localStorage` (`rebus_pencil_help_seen`) plus in-memory fallback for storage failures. Styled the new helper modal and updated toolbar/button CSS. Verified with `npm run build`. Tried a Playwright mobile sanity pass, but the app browser integration is configured for Chrome and local installation required sudo, so browser automation could not be completed in-session.
**Outcome:** success
**Insight:** mobile crossword UIs cannot treat clue auto-scroll and cell focus as harmless niceties; in stacked layouts they directly fight the primary input task unless scroll is constrained to an internal clue pane
**Promoted:** yes — see LESSONS_LEARNED "Auto-scrolling the active clue must be gated by a dedicated clue scroll container"

---

### [2026-03-23] — Extend overnight loop to include 15x15 and retune 15x15 search budget toward ~1 minute
**Context:** after validating that the new Rust phase-1 could solve `15x15`, user wanted `run_batch_loop.sh` to include `15x15` every cycle and explicitly allowed about one minute of runtime for that phase.
**Happened:** Updated `generator/core/size_tuning.py` so overnight defaults now run `7, 8, 9, 10, 11, 12, 15`, and reduced Python-side `15x15` `min_preparation_attempts` from `50` to `1` so adding `15x15` to the nightly loop does not trigger dozens of full define/rewrite retries before phase-1 quality is even known. Tuned Rust `15x15` settings in `crossword_engine/src/engine.rs`: first pass (`attempt_budget=70`) overshot to ~81.6s with no quality gain, so trimmed to `attempt_budget=50` while keeping the deeper `max_nodes=5_000_000`, `solved_candidates=3`, and `template_attempts=2_200`. Updated loop/default tests accordingly.
**Verification:** `python3 -m pytest tests/test_batch_publish.py tests/test_loop_controller.py -q` (`57 passed`); `cargo build --release --manifest-path crossword_engine/Cargo.toml`; direct `15x15` run with `crossword_engine/target/release/crossword_phase1 --size 15 --words generator/output/words.json --seed 1 --preparation-attempts 1` completed successfully in `65.55s` (`elapsed_ms=65033`, `solved_candidates=11`, `failed_templates=39`, same best score `572.442` as the longer 81.6s run).
**Outcome:** success
**Insight:** once phase-1 moves into a deep native search loop, old outer retry defaults can become the real time bomb — widen the inner search budget, but shrink legacy whole-pipeline retry floors so nightly loops stay proportional.
**Promoted:** no

---

### [2026-03-23] — Replace batch phase-1 grid generation with Rust binary and remove rarity from search
**Context:** user wanted crossword grid creation moved out of Python because candidate search was extremely slow and blocked deeper search. Requirements: keep `run_batch_loop.sh` as the real entrypoint, leave definition/UI flows untouched, make rarity irrelevant for phase-1 selection, and add maintainable/tests-first Rust infrastructure.
**Happened:** Added repo-local Rust crate `crossword_engine/` with a `crossword_phase1` binary. Implemented template generation, slot extraction, positional bitset index, DFS fill solver with MRV/forward checking, rarity-free word filtering/scoring, JSON stdout contract, and stderr progress logs. Updated `run_batch_loop.sh` to build the release binary before starting the Python loop. Refactored `generator/batch_publish.py` so production phase-1 now shells out to the Rust binary, reconstructs the existing Python `Candidate`/markdown shape from JSON, preserves downstream definition/rewrite/title/upload flow, and computes difficulty without rarity. Kept the old Python search path as `_best_candidate_python()` for tests and benchmarking. Added Rust unit/integration tests, Python wrapper tests, a shell-build smoke assertion, and `scripts/benchmark_phase1.py`.
**Verification:** `cargo test --manifest-path crossword_engine/Cargo.toml`; `cargo build --release --manifest-path crossword_engine/Cargo.toml`; `python3 -m pytest tests/test_batch_publish.py -q` (`48 passed`); `python3 -m pytest tests/test_constraint_solver.py tests/test_grid_template.py -q` (`31 passed`); `python3 scripts/benchmark_phase1.py --sizes 7 --seed 1` (`python_elapsed_sec=77.64`, `rust_elapsed_sec=1.313`, speedup `59.135x` on 7x7, artifact saved under `build/benchmarks/phase1/`).
**Outcome:** success
**Insight:** large native rewrites land cleanly when the old host-language boundary stays stable — keep Python-facing candidate/markdown contracts intact, swap only the hot phase behind a thin subprocess wrapper, and make the user entrypoint build the binary up front so overnight runs fail fast instead of degrading mid-pipeline.
**Promoted:** yes — see LESSONS_LEARNED entry on preserving host-language contracts for native hot-path migrations

---

### [2026-03-22] — Harden prompt-manifest anchor checks against already-landed replacements
**Context:** user reported a GitHub Actions failure in `tests/test_run_experiments.py::test_all_manifest_edit_anchors_exist_in_current_prompts`; CI showed `exp001` failing because `user/verify.md` already contained the shortened replacement line instead of the old longer anchor text.
**Happened:** Audited `scripts/run_experiments.py`, `tests/test_run_experiments.py`, and the live prompt files. Local prompt state still had the old anchor, but the failing CI snapshot matched the replacement text, so the brittle part was the invariant, not only the specific manifest entry. Updated `apply_experiment()` to treat `edit.replace` already being present as a clean skip even when `edit.find` is absent. Updated the anchor-regression test to accept either the current `find` anchor or the already-landed replacement text, and added a focused unit test covering the skip-on-replacement-present case with a temporary prompt directory. Verified with `python3 -m pytest tests/test_run_experiments.py -q`, `python3 -m pytest tests/test_verify.py -q`, and full `python3 -m pytest tests/ -q` (`353 passed`).
**Outcome:** success
**Insight:** manifest drift checks should enforce semantic applicability, not literal historical wording only; otherwise harmless baseline prompt cleanups become false-negative CI failures
**Promoted:** yes — see LESSONS_LEARNED "Prompt experiment runners/tests should accept replacement already present as a valid already-landed state"

---

### [2026-03-22] — Fix prompt autoresearch incumbent persistence and safe rebuild semantics
**Context:** first `prompt_autoresearch.py` tmux run executed one trial as intended with `--max-trials 1`, but durable state drifted: `state.json` still pointed at incumbent `81.9/0.386` while `incumbent.json` had regressed to the baseline `73.3/0.300`, making resume unsafe.
**Happened:** Audited `scripts/prompt_autoresearch.py`. Root cause: bootstrap replay produced the correct incumbent in memory, but a later persistence path could overwrite `incumbent.json` with stale baseline data. Refactored startup into explicit bootstrap/resume/rebuild flows, kept `persist_campaign_state()` as the only durable-write helper, and added validator-driven hybrid resume. Hardened `rebuild_state_from_campaign()` to rebuild in a temp directory and swap in only after success, then refresh the absolute incumbent snapshot path after the move. Added runtime error handling for rebuild failure, `--rebuild-state`, `--continuous`, richer `--status`, and tests covering mismatch detection, auto-rebuild, non-keep incumbent preservation, keep incumbent update, one-trial idle pointer semantics, and continuous run stop behavior. Rebuilt live `build/prompt_research/` from `logs/pilot_20260321.json` plus `build/assessment_runs/baseline_results_20260321_20260321_142651.json`.
**Verification:** `python3 -m py_compile scripts/prompt_autoresearch.py tests/test_prompt_autoresearch.py`; `python3 -m pytest tests/test_prompt_autoresearch.py tests/test_run_experiments.py tests/test_runtime_logging.py -q` (`34 passed`); `python3 scripts/prompt_autoresearch.py --rebuild-state --campaign-log logs/pilot_20260321.json --baseline-json build/assessment_runs/baseline_results_20260321_20260321_142651.json --dry-run`; `python3 scripts/prompt_autoresearch.py --status`.
**Outcome:** success
**Insight:** durable-state repair must be atomic; rebuilding in place can destroy the last good autoresearch state before replay succeeds.
**Promoted:** yes — see LESSONS_LEARNED entry on staged temporary rebuilds for durable state.

---

### [2026-03-24] — Replace hardcoded Rust size table with formula + dictionary-length pressure
**Context:** user challenged the strange `settings_for_size()` progression and explicitly called out word density by character count as another likely driver. Goal: stop hand-tuned jumps; make the scaling explainable from `7x7` upward; check whether length-bucket scarcity explains large-grid failures.
**Happened:** Replaced the Rust size match-table with formula-based scaling for target black density, node budget, attempt budget, two-letter tolerance, candidate-floor, and template attempts. Added dictionary-length pressure after loading filtered words: compare dense `5..8` buckets against the board's long-word buckets and nudge black budget / template budget / candidate floor accordingly. Added tests for monotonic settings and for sparse long buckets raising the black budget. Measured unique normalized counts by length from `generator/output/words.json` (`8-letter = 12481`, `15-letter = 546`) and ran a real `15x15` release probe on seed `42`.
**Verification:** `cargo test --manifest-path crossword_engine/Cargo.toml`; `cargo build --release --manifest-path crossword_engine/Cargo.toml`; direct release probe `crossword_engine/target/release/crossword_phase1 --size 15 --words generator/output/words.json --seed 42 --preparation-attempts 1`.
**Outcome:** partial
**Insight:** size-only settings were indeed misleading, and long-word scarcity is real, but the live `15x15` probe still failed quickly through black counts `44..52`; remaining top-end bottleneck is template/search topology, not just bad black-count scaling.
**Promoted:** yes — see LESSONS_LEARNED entry on size settings using dictionary length histograms, not board size alone.

---

### [2026-03-26] — Persist redefine/retitle run logs and force oldest-first maintenance ordering
**Context:** user wanted `run_definition_improve.sh` and title-regeneration runs to leave analyzable logs on disk, similar to long-running batch tooling, and asked why `run_definition_improve.sh` did not start from the oldest puzzle in Supabase.
**Happened:** Updated `generator/redefine.py` and `generator/retitle.py` so each run now creates a timestamped artifact dir under `generator/output/redefine_runs/` or `generator/output/retitle_runs/`, with `run.log` and `audit.jsonl`, and prints both paths at startup. Also changed puzzle fetch ordering in both tools to sort deterministically by `created_at` ascending with `id` as tie-break, instead of trusting unspecified Supabase row order. Added tests for oldest-first sorting and for persisted runtime log files.
**Verification:** `python3 -m pytest tests/test_runtime_logging.py tests/test_redefine.py tests/test_retitle.py -q` (`47 passed in 0.70s`)
**Outcome:** success
**Insight:** maintenance jobs need explicit artifact paths and explicit row ordering; otherwise logs disappear with the terminal session and DB iteration order becomes accidental behavior.
**Promoted:** no

---

### [2026-03-26] — Retitle only duplicate-name puzzles, prioritize worst duplicate clusters, enforce normalized title uniqueness
**Context:** user wanted retitling to attack repeated titles first, treating case and diacritics as irrelevant, and to stop accepting a regenerated title that normalizes to one already present elsewhere in the puzzle table. They also wanted title updates to bump `updated_at`.
**Happened:** Added a shared `normalize_title_key()` helper in `generator/phases/theme.py` that trims/collapses whitespace, strips trailing punctuation, and compares titles after Romanian diacritic collapse. Updated `generator/retitle.py` to select only puzzles whose normalized title currently appears multiple times globally, ordered by duplicate-cluster size descending and then oldest-first within each cluster. Passed the live set of other puzzle title keys into title generation so duplicate normalized titles are rejected during retries, and added a post-generation guard before DB update. Successful retitles now update both `title` and `updated_at`, and the in-memory title key set is refreshed after each accepted rename so later puzzles in the same run cannot collide with earlier renamed ones.
**Verification:** `python3 -m pytest tests/test_runtime_logging.py tests/test_retitle.py tests/test_theme.py -q` (`38 passed in 0.48s`)
**Outcome:** success
**Insight:** title dedup has to be run-stateful; if uniqueness is checked only against the pre-run DB snapshot, later puzzles can collide with titles minted earlier in the same maintenance batch.
**Promoted:** no

---

### [2026-03-26] — Preserve DEX usage-category headings in parsed definition text
**Context:** user noticed dexonline pages where register information lives in section headings like `Arhaisme și regionalisme`, not only inline markers such as `(reg.)`, and wanted that metadata to survive ingestion into `dex_definitions`.
**Happened:** Extended the HTML parser in `generator/core/dex_cache.py` so it still extracts compact `tree-def` synthesis definitions first, but also reads original-definition wrappers under usage-relevant callout headings and appends category-tagged entries like `Arhaisme și regionalisme: ...` to the parsed DEX text. Kept non-usage categories (e.g. `Sinonime`) out of this extra injection to avoid noisy prompt context. Added parser tests for usage-heading inclusion and non-usage exclusion.
**Verification:** `python3 -m pytest tests/test_dex_cache.py tests/test_ai_clues.py -q` (`117 passed in 9.40s`); `python3 -m pytest tests/test_verify.py -q` (`10 passed in 0.22s`)
**Outcome:** success
**Insight:** dexonline register metadata can sit outside the definition span; if ingestion reads only `tree-def`, it silently loses category-level rarity/register hints that matter downstream.
**Promoted:** no

---

### [2026-03-26] — Prioritize never-repaired puzzles in redefine maintenance runs
**Context:** user observed `run_definition_improve.sh` starting again from a puzzle repaired recently, while some puzzles still had never been repaired and still had null rebus/metadata fields in Supabase.
**Happened:** Changed `generator/redefine.py` sorting so maintenance runs now prioritize puzzles with `repaired_at IS NULL` first, then rows still missing puzzle metadata, then age. This pushes never-repaired / null-score puzzles ahead of recently repaired ones while preserving stable ordering within each bucket. Added a regression test covering the exact scenario of a recently repaired puzzle losing priority to a never-repaired puzzle with null metadata.
**Verification:** `python3 -m pytest tests/test_redefine.py -q` (`28 passed in 1.00s`)
**Outcome:** success
**Insight:** for maintenance jobs, plain chronological order is the wrong heuristic once repair state exists; `repaired_at` must dominate `created_at` or the queue re-chews fresh work while stale/null rows wait indefinitely.
**Promoted:** no

---

### [2026-03-22] — Regroup prompt autoresearch families so stale-stop does not kill unrelated hypothesis classes
**Context:** after one autonomous continuation, supervisor safe-stopped with `three consecutive stale families` even though only negative definition examples, early rate counterexamples, and one rewrite framing edit had actually been tested. Positive definition examples and rule/guidance variants were still untouched but hidden inside the same coarse family names.
**Happened:** Split experiment-family mapping in `scripts/run_experiments.py`: `rewrite_anti_distractor` into `rewrite_framing` vs `rewrite_structural_guidance`; `definition_examples` into `definition_negative_examples`, `definition_positive_examples`, `definition_guidance`; `rate_exactness` into `rate_counterexamples`, `rate_rules`. Updated family priorities in `generator/assessment/benchmark_policy.py` so the next live candidate is `definition_positive_examples`, not more negative examples or old verify work. Updated bundle unlock prerequisites to depend on the new finer-grained signal buckets. Adjusted autoresearch tests to assert the new first family/next experiment and rebuilt durable state from the same pilot log + baseline JSON.
**Verification:** `python3 -m py_compile scripts/run_experiments.py scripts/prompt_autoresearch.py tests/test_run_experiments.py tests/test_prompt_autoresearch.py generator/assessment/benchmark_policy.py`; `python3 -m pytest tests/test_run_experiments.py tests/test_prompt_autoresearch.py tests/test_runtime_logging.py -q` (`34 passed`); `python3 scripts/prompt_autoresearch.py --rebuild-state --campaign-log logs/pilot_20260321.json --baseline-json build/assessment_runs/baseline_results_20260321_20260321_142651.json --dry-run`; `python3 scripts/prompt_autoresearch.py --status`.
**Outcome:** success
**Insight:** stale-family logic only works if families correspond to hypothesis classes, not arbitrary contiguous manifest chunks.
**Promoted:** yes — see LESSONS_LEARNED entry on splitting prompt autoresearch families by hypothesis class.

---

### [2026-03-26] — Validate old DB titles before rerating and backfill missing `title_score` on retitle skips
**Context:** user caught a retitle inconsistency where garbage legacy titles like `"<|channel|>"` could receive absurd runtime scores (for example `10/10`) even though no such score existed in the database. Root cause: old titles without `title_score` were being sent straight to LLM rating without structural validation, and even legitimately computed old scores were discarded on `skipped, not better` paths instead of being persisted.
**Happened:** Updated `generator/retitle.py` so old titles now follow the same structural validation gate as new title candidates before any rerating. If the existing title is invalid (`titlu gol`, mixed language, etc.), retitle now assigns `old_score = 0` locally without calling the evaluator model. If `title_score` is missing but the old title is valid, the old score is computed as before; however, it is only backfilled to Supabase when the run ends up keeping the old title (`new_score <= old_score`). When the new title wins, the flow goes straight to the existing single final update and avoids a redundant preliminary write. Added coverage for invalid old titles, missing-score backfill on skip, and no-LLM rerating for structurally invalid titles.
**Verification:** `python3 -m pytest tests/test_retitle.py tests/test_theme.py tests/test_batch_publish.py tests/test_repair_puzzles.py -q` (`101 passed in 0.64s`); `python3 -m py_compile generator/retitle.py tests/test_retitle.py`
**Outcome:** success
**Insight:** maintenance code that compares new content against legacy DB content must validate the legacy side with the same structural rules first; otherwise the evaluator LLM can legitimize corrupt historical values and block repairs with bogus high scores.
**Promoted:** no

---

### [2026-03-26] — Reduce retitle model thrash with phase-batched title generation, keep batch publish path stable
**Context:** user provided live `run_title_improve.sh` logs showing expensive repeated LM Studio load/unload cycles, plus misleading `Model already active` lines that did not correspond to useful work. User wanted two things only: remove the redundant/log-noisy reactivation before rating, and batch title generation work so retitle can reuse loaded models across multiple puzzles, while explicitly not perturbing the initial publish path used by `run_batch_loop.sh`.
**Happened:** Cleaned `generator/phases/theme.py` so single-puzzle title generation no longer re-activates the generator model immediately before rating; log lines now identify both roles (`generator -> rated by evaluator`) instead of implying the current active model is always the generator. Added a retitle-specific batch runner in `generator/retitle.py`: batches default to 10 puzzles, load GPT once to generate for the whole batch, switch to Euro once to rate GPT candidates and generate Euro candidates, then switch back once to rate Euro candidates. This keeps the batching scoped to retitle only; `batch_publish` / `run_batch_loop.sh` still call the existing single-puzzle title flow in `theme.py`. Added parser support for `--batch-size`, extracted shared title-result application logic, and added tests for the reduced activation trace and the new retitle batch pipeline.
**Verification:** `python3 -m pytest tests/test_retitle.py tests/test_theme.py tests/test_batch_publish.py tests/test_repair_puzzles.py -q` (`99 passed in 0.46s`)
**Outcome:** success
**Insight:** performance batching for single-active-model local runtimes belongs at the orchestration boundary that owns many items (`retitle` here), not necessarily inside the generic per-item generation primitive used by unrelated pipelines.
**Promoted:** no

---

### [2026-03-26] — Make `run_title_improve.sh` / `retitle --all` process all puzzles, prioritize missing title scores
**Context:** user noticed `./run_title_improve.sh` only touched the duplicate-title subset (`170` rows), while the intent for `--all` was literal full-table retitle ordered by age, with extra urgency for rows that still have no `title_score`.
**Happened:** Changed `generator/retitle.py` so `select_puzzles_for_retitle(...)` now orders all candidate rows by `(missing title_score first, created_at oldest first, id)` instead of silently filtering to duplicate normalized titles. Preserved the old duplicate-only behavior behind a new explicit `--duplicates-only` flag via `select_duplicate_puzzles_for_retitle(...)`. Updated the CLI error/help text and run output to mention the number of prioritized missing-score puzzles. `run_title_improve.sh` needed no change because it already invokes `generator.retitle --all`; the semantics of `--all` now finally match the wrapper name.
**Verification:** `python3 -m pytest tests/test_retitle.py tests/test_theme.py tests/test_batch_publish.py tests/test_repair_puzzles.py -q` (`97 passed in 0.66s`); `python3 -m py_compile generator/retitle.py tests/test_retitle.py`
**Outcome:** success
**Insight:** command flags that say `--all` should not hide a second-stage subset filter; if product wants a narrower maintenance mode, it needs its own explicit flag or operators will debug the wrong layer.
**Promoted:** no

---

### [2026-03-26] — Repair title dual-generator orchestration, per-model retries, and prompt shaping
**Context:** user ran `./run_title_improve.sh` and surfaced live logs showing `gpt-oss-20b` labeled generation attempts happening after the runtime had already unloaded GPT and loaded EuroLLM, plus repeated empty GPT outputs and EuroLLM repeatedly emitting overlong mixed-quality titles. Goal: keep both models as generators, but make orchestration truthful and retries corrective instead of noisy.
**Happened:** Refactored `generator/phases/theme.py` title generation loop to stop preactivating both models up front. Each generator is now activated immediately before its own LLM call, and the cross-model evaluator is activated only after a candidate passes structural validation. Added per-model rejected-history shaping so corrective hints are scoped to the generator that actually failed; repeated failures like `prea multe cuvinte` now inject short corrective instructions (`maximum 5 cuvinte`) into later prompts for that same model. Empty generator output now triggers one short retry without dragging along the full rejected-history, and empty results no longer pollute semantic rejection context with `"(gol)"`. Tightened generation prompts to explicitly require Romanian-only 2-5 word titles, ban comma/coordinated forms, and show positive/negative examples. Expanded theme tests to cover no-preactivation ordering, empty-output retry isolation, repeated-invalid hinting, and mixed-language rejection.
**Verification:** `python3 -m pytest tests/test_theme.py -q` (`30 passed in 0.30s`); `python3 -m pytest tests/test_retitle.py tests/test_repair_puzzles.py tests/test_batch_publish.py tests/test_ai_clues.py -q` (`118 passed in 0.66s`)
**Outcome:** success
**Insight:** in a local single-active-model runtime, “which model is generating now?” is orchestration state, not just metadata; preactivation can silently falsify logs and waste debugging effort by making prompt failures look like model-quality failures.
**Promoted:** yes — see LESSONS_LEARNED entry on just-in-time multi-model activation.

---

### [2026-03-28] — Only count meaningful puzzle progress after at least one filled letter
**Context:** user reported that simply opening a puzzle and backing out marked it as `in progress`, even though no letter had been filled.
**Happened:** Updated `frontend/src/gamification/progress-storage.ts` with a `hasFilledCells()` helper and aligned the meaning of progress with “at least one non-empty cell”. Updated `frontend/src/main.ts` so empty progress snapshots are cleared instead of saved, browse-state derivation only marks puzzles `in_progress` when saved progress has at least one filled cell, and old empty saved entries are cleaned up on load.
**Verification:** `npm run build` in `frontend/`.
**Outcome:** success
**Insight:** local progress keys should not be treated as progress by existence alone; resume/status semantics need a content-based threshold, otherwise navigation side effects masquerade as user intent.
**Promoted:** no

---

### [2026-03-28] — Collapse selector filters behind compact disclosure on mobile
**Context:** after the broader selector refresh landed, user reported the filter area still occupied too much vertical space on phone and explicitly asked for a more compact pattern, suggesting a dropdown-style control.
**Happened:** Reworked `frontend/src/components/puzzle-selector.ts` so the selector now defaults to a compact top row: `Filtre (n)` disclosure button plus a shortened sort dropdown (`Recente`, `Mărime ↑`, `Mărime ↓`, `A-Z`). The full status/size/hide/reset controls now live inside an expandable panel instead of always-visible pills. Updated `frontend/src/styles/gamification.css` and `frontend/src/styles/responsive.css` to support the disclosure layout, compact widths, and shorter mobile labels. This replaced the earlier “always-open chip wall” with a mobile-first disclosure pattern while keeping the same filter capabilities.
**Verification:** `npm run build` in `frontend/`.
**Outcome:** success
**Insight:** dense mobile filters should default to a disclosure + summary pattern; even well-styled chips still cost too much vertical space when the filter set is more than 2-3 controls.
**Promoted:** no

---

### [2026-03-28] — Refresh frontend puzzle discovery, local progress view, and lightweight challenges
**Context:** user wanted the frontend menu to scale better past 300 puzzles, with size-first browsing, status filters, a reset-all affordance, preserved list state when returning from a puzzle, mobile-safe controls, a clearer local progress/profile view, and lightweight local-only challenges instead of a misleading “leaderboard”.
**Happened:** Reworked the frontend selector shell in `frontend/index.html` and moved list derivation into `frontend/src/main.ts` with one explicit browse state (`status`, `hideCompleted`, `sizeGroup`, `sort`). Refactored `frontend/src/components/puzzle-selector.ts` into a render-only browse UI with status chips, size-group chips, sort select, reset button, results summary, continue section, challenge highlight, explicit empty state, and clearer puzzle cards. Added `frontend/src/gamification/challenges.ts` to derive local challenge status from existing player/progress data, plus backward-compatible `checksUsed` support in `frontend/src/gamification/storage.ts` and `frontend/src/gamification/progress-storage.ts` so a “fără verificare” challenge can be tracked going forward. Updated play-view metadata in `frontend/src/main.ts` to surface size/status/resume context and preserve browse state/scroll position when returning to the list. Reframed `frontend/src/components/stats-panel.ts` into a progress view with points, solved, in-progress, best time, challenge cards, badges, and personal history. Updated selector/game/profile styling across `frontend/src/styles/main.css`, `frontend/src/styles/gamification.css`, `frontend/src/styles/grid.css`, and `frontend/src/styles/responsive.css`, and refreshed onboarding copy in `frontend/src/components/tutorial.ts`.
**Verification:** `npm run build` in `frontend/` twice after the main patch and after the mobile-chip layout cleanup. Manual browser verification was attempted but blocked in this workspace because Playwright's browser install path requires local browser setup/root prompts that are not available here.
**Outcome:** success
**Insight:** none beyond implementation-specific UI polish.
**Promoted:** no

---

### [2026-03-23] — Add first-round hybrid de-anchoring to redefine/repair without new prompts
**Context:** user wanted `redefine`/repair to reduce anchoring bias from bad existing clues, but explicitly without adding or editing prompt files because prompt optimization is already active on the existing prompt set.
**Happened:** Extended `generator/core/rewrite_engine.py` with an optional `hybrid_deanchor` path. For clearly bad existing clues (`verified == False` or `rebus_score <= 4`), at the first rewrite opportunity only, the engine now builds two candidates using the existing prompt stack: one via `rewrite_definition(...)`, one via fresh `generate_definition(...)`. Both candidates are verified/rated with the current evaluator flow, compared via the existing clue-selection logic, and the winning branch is kept. Added bookkeeping so the fresh branch only runs once per clue, plus branch logging (`rewrite_only`, `fresh_only`, `rewrite`, `fresh_generate`). Enabled the option in `generator/redefine.py` and `generator/repair_puzzles.py`, while leaving batch publish unchanged. Added targeted engine tests for failed clues, low-rebus verified clues, no-hybrid on `rebus >= 5`, first-round-only behavior, branch winner selection, one-branch-valid fallback, and both-branches-no-op behavior.
**Verification:** `python3 -m py_compile generator/core/rewrite_engine.py generator/redefine.py generator/repair_puzzles.py tests/test_rewrite_engine.py`; `python3 -m unittest tests.test_rewrite_engine tests.test_redefine tests.test_repair_puzzles`.
**Outcome:** success
**Insight:** de-anchoring can be introduced as control-flow around existing prompt families; no prompt-text change is needed if candidate generation branches are compared through the same downstream verifier/rater and selector.
**Promoted:** no

---

### [2026-03-23] — Implement repair pipeline for published puzzles with score backfill and dual timestamps
**Context:** user wanted an automated repair job for existing published rebusuri: prioritize missing-score puzzles first, then lowest-score oldest puzzles; regenerate clues/title only when the new puzzle-level rebus score improves; expose both creation and last-repair timestamps in product/UI; avoid mixing prompt versions mid-run.
**Happened:** Added DB metadata fields in `schema.sql` plus a migration (`migrations/20260323_add_repair_metadata.sql`) for `description`, numeric puzzle scores, and `updated_at`/`repaired_at`. Extracted shared puzzle scoring into `generator/core/puzzle_metrics.py` and prompt preload/audit helpers into `generator/core/prompt_runtime.py`. Updated `generator/phases/upload.py` and `generator/batch_publish.py` so new uploads persist deterministic `description` + numeric puzzle metrics without stuffing score text into legacy `theme`. Implemented `generator/repair_puzzles.py`: published-only queue ordering, baseline re-evaluation, metadata backfill for unscored rows, rewrite/title regeneration with strict `min_rebus` acceptance gate, and accepted-state writes for puzzle metadata plus clue `definition`/`verify_note`/`verified`. Updated worker/frontend to return and display `description`, `created_at`, and `repaired_at`, sort by `repaired_at ?? created_at`, and show both dates in list/detail surfaces. Fixed a latent adapter bug in `generator/redefine.py`: DB clue directions stored as `H`/`V` were being misread because the adapter only recognized `"vertical"`.
**Verification:** `python3 -m py_compile generator/batch_publish.py generator/redefine.py generator/repair_puzzles.py generator/core/puzzle_metrics.py generator/core/prompt_runtime.py`; `python3 -m unittest tests.test_redefine tests.test_repair_puzzles tests.test_batch_publish`; `npm run build` in `frontend/`. Worker-specific typecheck via local TypeScript compiler was blocked because `worker/node_modules` is not installed in this workspace (`@cloudflare/workers-types` missing).
**Outcome:** success
**Insight:** DB adapters for clue coordinates/direction are part of the publishing contract; treat compact persisted enums (`H`/`V`) as first-class, not as legacy edge cases.
**Promoted:** yes — see LESSONS_LEARNED entry on accepting persisted `H`/`V` direction codes.

---

### [2026-03-23] — Archive v1 prompt campaign, add fragile-word guardrails, bootstrap narrow v2 campaign
**Context:** user asked to freeze the current v1 campaign, save the active ledger as `results5.tsv`, keep the `exp002` incumbent as seed, add fragile-word guardrails to evaluation, and start a separate narrow v2 prompt campaign with a new state dir and a 12-experiment manifest.
**Happened:** Extended `generator/assessment/benchmark_policy.py` with primary/secondary fragile-word watchlists plus tighter v2 family stop thresholds. Refactored `scripts/run_experiments.py` to support experiment namespaces: preserved the original 100-experiment v1 manifest, added a separate 12-experiment `v2` manifest (`v2exp001..v2exp012`), added `--experiment-set`, and made the classifier mark primary fragile-word losses as immediate `discard`. Refactored `scripts/prompt_autoresearch.py` to store `experiment_set` in durable state, build family graphs per set, and use set-specific stale thresholds. Archived `generator/assessment/results.tsv` to `generator/assessment/results5.tsv`, reset `results.tsv` to header plus the incumbent `exp002` keep row, restored live prompts from the v1 incumbent snapshot, and bootstrapped `build/prompt_research_v2/` from `build/prompt_research/incumbent.json`. Verified that v2 status is valid and next experiment is `v2exp001`.
**Verification:** `python3 -m py_compile scripts/run_experiments.py scripts/prompt_autoresearch.py generator/assessment/benchmark_policy.py tests/test_run_experiments.py tests/test_prompt_autoresearch.py`; `python3 -m pytest tests/test_run_experiments.py tests/test_prompt_autoresearch.py tests/test_runtime_logging.py -q` (`37 passed`); `python3 scripts/run_experiments.py --experiment-set v2 --dry-run`; `python3 scripts/prompt_autoresearch.py --state-dir build/prompt_research_v2 --baseline-json build/prompt_research/incumbent.json --experiment-set v2 --description-prefix autoresearch_v2/ --dry-run`; `python3 scripts/prompt_autoresearch.py --state-dir build/prompt_research_v2 --status`.
**Outcome:** success
**Insight:** once the repeated loser cluster is known, it should move from postmortem knowledge into the live classifier and family-stop policy before the next prompt campaign starts.
**Promoted:** yes — see LESSONS_LEARNED entry on explicit fragile-word watchlists.

---

### [2026-03-26] — Persist title scores and tighten title screening across retitle + initial publish flows
**Context:** user wanted title regeneration to persist a `title_score`, accept immediately only at `8/10`, reject title candidates that are `ALL CAPS`, `6+` words, or leak normalized solution words of length `3+` (while allowing 2-letter solution words), and keep local schema/docs aligned with the live Supabase column they had already added manually.
**Happened:** Added shared normalized-text helpers in `generator/core/text_rules.py` and refactored `generator/phases/theme.py` so title generation now distinguishes candidate review from fallback selection. Invalid title candidates now surface explicit rejection reasons instead of collapsing immediately into random fallback titles; `TITLE_MIN_CREATIVITY` moved to `8`, the soft word-count cap moved to `5`, and solution-word leakage now checks normalized token overlap with `min_length=3`. Added structured `TitleGenerationResult` plumbing, persisted `title_score` in `generator/retitle.py`, and propagated the same score into initial publish (`generator/batch_publish.py` via upload metadata) and repair acceptance (`generator/repair_puzzles.py`). Updated local schema/docs (`schema.sql`, `README.md`, `GENERATOR_ARCH.md`) to reflect the live `title_score` column, and expanded tests for helper normalization, title validation, stored-score reuse, repair persistence, and batch title generation wiring.
**Verification:** `python3 -m pytest tests/test_text_rules.py tests/test_theme.py tests/test_retitle.py tests/test_repair_puzzles.py tests/test_batch_publish.py -q` (`90 passed in 0.46s`); `python3 -m py_compile generator/core/text_rules.py generator/phases/theme.py generator/retitle.py generator/repair_puzzles.py generator/batch_publish.py tests/test_text_rules.py tests/test_theme.py tests/test_retitle.py tests/test_repair_puzzles.py tests/test_batch_publish.py`
**Outcome:** success
**Insight:** title validation rules that affect acceptance thresholds and persistence need to live before scoring, but fallback titles should remain a last-resort output only; otherwise maintenance and initial publish paths diverge and score metadata becomes meaningless.
**Promoted:** yes — see LESSONS_LEARNED entry on separating title screening from fallback selection.

---

### [2026-03-29] — Point working policy at `baseline_results_20260329_v4exp001`
**Context:** user confirmed the fresh baseline run on the adopted `v4exp001` prompt had been recorded in `generator/assessment/results.tsv`, so the policy label needed to stop pointing at the older March 28 baseline.
**Happened:** Updated `WORKING_BASELINE_DESCRIPTION` in `generator/assessment/benchmark_policy.py` to `baseline_results_20260329_v4exp001` and aligned `tests/test_benchmark_policy.py` string expectations to the new incumbent label.
**Verification:** `.venv/bin/python -m pytest tests/test_benchmark_policy.py tests/test_run_experiments.py tests/test_prompt_autoresearch.py` (`52 passed`).
**Outcome:** success
**Insight:** once a fresh incumbent baseline row is written after ledger rotation, update the working baseline label immediately; otherwise autoresearch will compare future experiments against the wrong named control even if `results.tsv` is current.
**Promoted:** no

---

### [2026-03-29] — Apply phase-specific reasoning profiles for GPT-OSS
**Context:** after LM Studio restart, `/api/v1/models` finally exposed `capabilities.reasoning` for `openai/gpt-oss-20b`, and the user wanted reusable request-time reasoning configuration with stronger effort on generation/rewrite/rating.
**Happened:** Updated the centralized chat helper path so `gpt-oss` now uses `reasoning_effort="medium"` for definition generate/rewrite/rate calls, `reasoning_effort="low"` for verify/tiebreak/title calls, and no reasoning parameters for `eurolllm`. Also normalized the long-output cap from `2048` to `2000` tokens in definition generation and title generation. Kept `reasoning_tokens` unset because live LM Studio chat-completions behavior did not respect it predictably and unsupported models (`eurolllm`) still 500 when receiving reasoning params.
**Verification:** `.venv/bin/python -m pytest tests/test_model_manager.py tests/test_ai_clues.py tests/test_theme.py tests/test_run_assessment.py` (`109 passed`). Live sanity check against `http://127.0.0.1:1234/v1/chat/completions` returned `OK` for `gpt-oss` with `reasoning_effort=medium` and showed higher `completion_tokens_details.reasoning_tokens`.
**Outcome:** success
**Insight:** once per-model reasoning controls exist, the useful granularity is phase-level, not model-level only — short deterministic verifier/tiebreak calls should stay cheap, while longer creative/analytic passes can spend more reasoning budget.
**Promoted:** no

---

### [2026-03-29] — Add `reasoning_effort=low` for GPT-OSS chat completions
**Context:** user installed an LM Studio update that accepts `reasoning_effort` on OpenAI-compatible `/v1/chat/completions` and wanted `gpt-oss` calls to default to low effort.
**Happened:** Extended `generator/core/model_manager.py` with optional per-model `reasoning_effort`, set `PRIMARY_MODEL` (`openai/gpt-oss-20b`) to `low`, and added `chat_reasoning_options()` lookup. Routed clue-generation, rewrite, verify, rate, tiebreak, and theme/title chat calls through a shared helper in `generator/core/ai_clues.py` so LM Studio requests include `reasoning_effort` only for configured models. Added payload tests in `tests/test_model_manager.py`, `tests/test_ai_clues.py`, and `tests/test_theme.py`.
**Verification:** `.venv/bin/python -m pytest tests/test_model_manager.py tests/test_ai_clues.py tests/test_theme.py tests/test_run_assessment.py` (`107 passed`). Also checked `curl -s http://127.0.0.1:1234/api/v1/models`; current live response still does not expose a `reasoning` field, so dynamic autodetect is not yet available from the running server.
**Outcome:** success
**Insight:** when a serving layer gains request-time model controls before it reliably exposes capability metadata, the safest integration is static per-model config plus a single request helper, not speculative autodetect spread across call sites.
**Promoted:** no

---

### [2026-03-29] — Confirm `v4exp001`, prepare `v5`, rotate ledger for fresh incumbent baseline
**Context:** the three-run confirmation series for `v4exp001` finished and showed stable gains over the previous baseline, so the next move was to lock in a clean baseline workflow and start a new batch that isolates the useful framing signal seen in near-miss `v4exp004`.
**Happened:** Read the confirmation rows (`77.0`, `76.9`, `73.8`; average composite `75.9`, average pass `0.333`) and kept `v4exp001` as the working prompt. Added a new `v5` experiment set in `scripts/run_experiments.py`, `generator/assessment/benchmark_policy.py`, and `scripts/prompt_autoresearch.py` with eight rewrite-only probes: header-signal isolation, header blends, and precision-support lines. Updated `prompt_research.md` for the new baseline/runbook, archived the completed ledger to `generator/assessment/results8.tsv`, and reset `generator/assessment/results.tsv` to header-only so the next official incumbent baseline can be recorded cleanly.
**Verification:** `.venv/bin/python scripts/run_experiments.py --experiment-set v5 --dry-run`; `.venv/bin/python -m pytest tests/test_run_experiments.py tests/test_prompt_autoresearch.py tests/test_benchmark_policy.py tests/test_run_assessment.py tests/test_selection_engine.py` (`57 passed`).
**Outcome:** success
**Insight:** confirmation-series rows should not be left as the live incumbent ledger for future experiments; rotate them into an archive and record one dedicated baseline row for the adopted prompt before starting the next batch.
**Promoted:** no

---

### [2026-03-28] — Fix equivalent-definition selection bias and add tier-balanced pass metric
**Context:** after `v4`, review suggested two follow-ups before changing experiment policy: avoid undercounting passes when pass1/pass2 produce the same normalized definition, and expose a secondary pass metric that does not let the larger low/medium tiers dominate interpretation.
**Happened:** Updated `generator/core/selection_engine.py` so `choose_clue_version()` no longer auto-picks variant A when two definitions normalize to the same text; it now prefers the stronger assessed version, including verified-over-unverified. Added `tier_balanced_pass_rate` to `generator/assessment/run_assessment.py` output and console reporting, defined as the mean of present per-tier pass rates, while keeping canonical `pass_rate` and composite unchanged.
**Verification:** `.venv/bin/python -m pytest tests/test_selection_engine.py tests/test_run_assessment.py tests/test_run_experiments.py tests/test_prompt_autoresearch.py` (`49 passed`).
**Outcome:** success
**Insight:** assessment can silently undercount real wins if equivalent clue texts default to the earlier pass instead of the better-verified pass; normalize-first selection still needs assessment-aware tie resolution.
**Promoted:** no

---

### [2026-03-29] — Reset benchmark regime, refresh assessment DEX, and open `v6`
**Context:** after `v5` went `8/8 discard`, user wanted the benchmark lane reset around replicated evidence, fresh DEX text, and a new verify/rate/definition batch instead of more rewrite-first tuning.
**Happened:** Updated `generator/assessment/run_assessment.py` to refresh `dex_definitions` through `DexProvider` cache/Supabase lookup before falling back to `dataset.json`. Refactored `scripts/run_experiments.py` to compare candidates through replicated incumbent/candidate batches (`--comparison-runs`, default `3`), emit machine-readable comparison summaries under the assessment log dir, and use `tier_balanced_pass_rate` in keep/discard logic instead of single-run composite alone. Added `v6` with 8 experiments focused on `verify`, `rate`, then `definition`; wired policy/autoresearch/docs/tests for `v6`; expanded historical-evidence policy to `results1.tsv` through `results8.tsv`.
**Verification:** `.venv/bin/python -m pytest tests/test_run_assessment.py tests/test_run_experiments.py tests/test_prompt_autoresearch.py tests/test_benchmark_policy.py` (`61 passed`); `python3 -m py_compile generator/assessment/run_assessment.py scripts/run_experiments.py scripts/prompt_autoresearch.py`; `.venv/bin/python scripts/run_experiments.py --experiment-set v6 --dry-run`.
**Outcome:** success
**Insight:** once benchmark semantics drift, runner logic must stop treating old ledger highs or one noisy run as the source of truth; replicated machine-readable comparisons and refreshed upstream context need to become the default control surface.
**Promoted:** yes — added reset-regime lesson to `LESSONS_LEARNED.md`

---

### [2026-03-28] — Reframe `v4` rewrite experiments away from negative banned-token phrasing
**Context:** user flagged a plausible local-model failure mode: negated wording like “nu folosești engleză” can still bias weaker models toward the forbidden token just by mentioning it.
**Happened:** Rewrote the `v4` manifest in `scripts/run_experiments.py` so the exploratory rewrite variants now use positive Romanian-register, referent-first, and lexical-distance phrasing instead of explicit negative bans mentioning the unwanted token. Updated `prompt_research.md` to record the new hypothesis and `v4` probe descriptions.
**Verification:** `.venv/bin/python scripts/run_experiments.py --experiment-set v4 --dry-run`; `.venv/bin/python -m pytest tests/test_run_experiments.py tests/test_prompt_autoresearch.py tests/test_benchmark_policy.py` (`50 passed`).
**Outcome:** success
**Insight:** prompt variants for older local models should prefer positive target-state phrasing over negated forbidden-token wording when the forbidden token itself can anchor generation.
**Promoted:** no

---

### [2026-03-28] — Promote `v3exp016` baseline to working incumbent and prep `v4`
**Context:** user ran fresh baseline on the confirmed `v3exp016` rewrite prompt and wanted the benchmark lane aligned before launching the next experiment batch.
**Happened:** Verified new kept ledger row in `generator/assessment/results.tsv` (`baseline_results_20260328_v16`, composite `72.7`, pass rate `0.300`), updated `generator/assessment/benchmark_policy.py` to point `WORKING_BASELINE_DESCRIPTION` at that baseline, and refreshed `tests/test_benchmark_policy.py` so baseline-name assertions match the new incumbent.
**Verification:** `.venv/bin/python -m pytest tests/test_benchmark_policy.py tests/test_run_experiments.py tests/test_prompt_autoresearch.py tests/test_run_assessment.py` (`51 passed`).
**Outcome:** success
**Insight:** after rotating `results.tsv` for a fresh baseline, policy constants that name the incumbent must be updated immediately; otherwise manual runs and automated selection disagree about which prompt is the control.
**Promoted:** no

---

### [2026-03-30] — Raise rewrite/rate completion budgets for LM Studio medium reasoning
**Context:** today’s redefine run (`20260329_153843`) collapsed candidate scores and produced mass `too short (0 chars)` / `JSON invalid` failures right after LM Studio started honoring `reasoning_effort`.
**Happened:** Compared yesterday vs today logs, traced the regression to `gpt-oss` rewrite/rate calls using `reasoning_effort="medium"` with old low completion budgets, reproduced the exact failure mode live on `PROMPT` and `ABA`, then updated `generator/core/ai_clues.py` so `rewrite_definition()` and `rate_definition()` now send `max_tokens=2000`. Added truncation logging that records `purpose`, `model`, `max_tokens`, `completion_tokens`, and `reasoning_tokens` when a call ends with `finish_reason="length"`. Updated `tests/test_ai_clues.py` expectations for the new budgets.
**Verification:** `python3 -m pytest tests/test_ai_clues.py tests/test_model_manager.py -q` (`77 passed`); live checks via `rewrite_definition()` / `rate_definition()` on `PROMPT` and `ABA` returned valid text / valid `DefinitionRating` objects instead of empty outputs.
**Outcome:** success
**Insight:** once LM Studio starts honoring reasoning params, old completion budgets may become invalid immediately because reasoning tokens consume the same completion budget; phase-specific retuning is mandatory.
**Promoted:** yes — see LESSONS_LEARNED entry on LM Studio reasoning and completion budgets.

---

### [2026-03-31] — Make crossword grid letters bold
**Context:** user wanted the letters entered into the crossword grid to read more strongly.
**Happened:** Updated `frontend/src/styles/grid.css` so `.cell__input` now uses `font-weight: 700` instead of the previous semi-bold setting.
**Verification:** `npm run build` in `frontend/` (`tsc && vite build`, pass).
**Outcome:** success
**Insight:** crossword cell typography benefits from decisively heavier weight than surrounding UI copy; half-bold can still look washed out once the grid borders and number marks compete for contrast.
**Promoted:** no

---

### [2026-03-31] — Stabilize definition bar height and auto-shrink long clue text
**Context:** user wanted the active clue box above the grid to always reserve room for three text rows, shrink long clue text until it fits, stop making the grid jump vertically, and remove the `16/44` counter from between the clue box and the grid.
**Happened:** Updated `frontend/src/styles/gamification.css` so the definition bar now has a stable three-line height and the clue text is constrained to a fixed three-line text box. Extended `frontend/src/components/definition-bar.ts` with a small font-fit loop that starts from the normal clue font size and steps downward until the clue text fits inside that three-line area. Hid `progress-counter` in `frontend/src/styles/grid.css` so the `16/44` indicator no longer appears between the clue box and the grid. Added a lightweight `resize` refresh hook in `frontend/src/main.ts` so the definition text is re-fitted when the viewport width changes.
**Verification:** `npm run build` in `frontend/` (`tsc && vite build`, pass).
**Outcome:** success
**Insight:** if dynamic clue text lives above a fixed-aspect-ratio grid, the safest way to avoid layout jitter is to stabilize the container height first and only then fit the typography into that box; doing only one of the two still causes visible vertical movement.
**Promoted:** no

---

### [2026-03-31] — Make crossword backspace retreat to the previous square
**Context:** user clarified that the backspace behavior on the custom keyboard should move back to the previous crossword square instead of acting like a stationary delete on the current one.
**Happened:** Updated `frontend/src/components/input-handler.ts` so `Backspace` now behaves like crossword backspace: if the current cell has a letter, it clears it and moves to the previous square on the active direction; if the current cell is already empty, it moves back first and clears that previous square. Wired the touch remote in `frontend/src/main.ts` to use the same shared backspace helper, and updated the touch keyboard button copy in `frontend/index.html` to describe backspace semantics instead of plain delete semantics.
**Verification:** `npm run build` in `frontend/` (`tsc && vite build`, pass).
**Outcome:** success
**Insight:** the virtual keyboard should reuse the same editing primitive names as the physical keyboard; if a `⌫` button is implemented as `Delete`, users notice the mismatch immediately because the crossword cursor model makes backtracking part of the core typing flow.
**Promoted:** no

---

### [2026-03-31] — Run periodic maintenance on agent memory/config files
**Context:** user asked for the periodic maintenance pass described by the AI agent config guide: audit `AGENTS.md`, `CLAUDE.md`, `LESSONS_LEARNED.md`, `ITERATION_LOG.md`, and sub-agents for stale references, overlap, and hierarchy violations.
**Happened:** Audited root memory files and `.claude/agents/`. Confirmed `CLAUDE.md` is still the required one-line redirect, sub-agent table matches the actual `.claude/agents/` directory, and all current agent files stay under the intended size envelope. Found one concrete integrity bug: `AGENTS.md` referenced `SETUP_AI_AGENT_CONFIG.md`, but that file did not exist in the repo. Added `SETUP_AI_AGENT_CONFIG.md` with the full setup + periodic maintenance protocol so the reference is now valid and future maintenance has a canonical on-disk source. No `AGENTS.md` growth was needed, and no obvious stale lessons or agent-file mismatches required cleanup in this pass.
**Outcome:** success
**Insight:** periodic maintenance should prioritize structural integrity fixes first; a broken reference in `AGENTS.md` is higher impact than micro-pruning wording because it removes the maintenance protocol from the memory graph entirely.
**Promoted:** no

---

### [2026-03-31] — Add upload timestamps, canonical backfill wrapper, and mobile pencil emoji
**Context:** after the touch remote work, user called out three earlier items still pending: new puzzle uploads were not setting `updated_at`, there was no obvious repo-root shell wrapper for canonical clue backfill, and the compact mobile toolbar still needed the pencil control rendered as an emoji-only button.
**Happened:** Updated `generator/phases/upload.py` to stamp both `created_at` and `updated_at` with the same fresh UTC ISO timestamp on insert and corrected the post-upload activate command hint to `python -m generator activate ...`. Added `run_clue_canon_backfill.sh` at repo root as a thin executable wrapper around `python -m generator.clue_canon backfill`, with repo-root `cd`, `.venv/bin/python` preference, `--apply` default injection, passthrough args, and a short `--help` usage block. Added `tests/test_upload_phase.py` to assert that fresh uploads set both timestamps identically. Updated `README.md` with dry-run, apply, and targeted-word wrapper examples. Finished the mobile toolbar tweak by adding a `✏️` icon span to `frontend/index.html`, mobile-only icon display in `frontend/src/styles/grid.css` / `frontend/src/styles/responsive.css`, and synced `aria-label` updates in `frontend/src/main.ts`.
**Verification:** `python3 -m unittest tests.test_upload_phase` (`OK`); `bash -n run_clue_canon_backfill.sh`; `./run_clue_canon_backfill.sh --help`; `npm run build` in `frontend/` (`tsc && vite build`, pass).
**Outcome:** success
**Insight:** when a repo is mid-refactor, the safest way to add operator-facing entrypoints is to wrap the current canonical command exactly as-is and document the wrapper, rather than inventing a second orchestration path that can drift from the live flow.
**Promoted:** no

---

### [2026-03-31] — Add touch-only crossword remote with synced direction control
**Context:** user wanted phone/tablet play to stop invoking the OS keyboard and instead use an on-screen QWERTY remote under the grid, plus a direction-toggle icon that always stays in sync with the active clue orientation.
**Happened:** Updated `frontend/index.html` with a dedicated `touch-remote` control block (`QWERTY` / `ASDFGHJKL` / `direction + ZXCVBNM + delete`). Extended `GridState` in `frontend/src/components/grid-renderer.ts` with `touchRemoteEnabled`, made touch-mode grid inputs `readOnly` with `inputMode="none"`, and changed focus handling so touch mode focuses the cell shell instead of the native input. Refactored `frontend/src/components/input-handler.ts` to expose shared virtual actions (`handleVirtualLetter`, `deleteActiveCell`, `toggleDirection`) and wired them from `frontend/src/main.ts`, where the remote rerenders on every `refresh()` so the direction icon reflects `activeDirection` no matter whether orientation changed via remote button, repeated tap on the same cell, or clue click. Compressed the top toolbar to one mobile row and added new remote styles in `frontend/src/styles/grid.css` and `frontend/src/styles/responsive.css`.
**Verification:** `npm run build` in `frontend/` (`tsc && vite build`, pass). Tried a live mobile smoke test via Playwright MCP after starting a local Vite server, but the session browser path expected a local Chrome install that is not available in this workspace, so no automated visual browser verification completed.
**Outcome:** success
**Insight:** when a touch UI must suppress the OS keyboard without forking gameplay logic, the stable pattern is to keep grid state and direction semantics shared, make native inputs inert only at the rendering layer, and route virtual keyboard actions through the same state transitions as physical keyboard input.
**Promoted:** no

---

### [2026-03-30] — Show full solution when reopening a solved puzzle in the UI
**Context:** user reported that opening a puzzle from the solved tab showed an empty grid instead of the finished answer.
**Happened:** Traced the bug to `frontend/src/main.ts`: solved puzzles skip `loadProgress()`, but the fetched `/solution` payload was only stored in `gridState.solution` for hint/check logic while the renderer displays `gridState.cells`. Added a solved-view path that hydrates `cells` from `solution`, marks all letter cells as revealed, clears pencil marks, flips a new `isSolvedView` flag on `GridState`, disables toolbar actions, and makes grid inputs `readOnly`. Added small disabled/readonly styling in `frontend/src/styles/grid.css`.
**Verification:** `npm run build` in `frontend/` (`tsc && vite build`, pass).
**Outcome:** success
**Insight:** if UI rendering uses editable cell state separate from canonical solution state, solved-history reopen flows must explicitly hydrate the visible cells; attaching the solution only for hint logic is not enough.
**Promoted:** no

---

### [2026-03-31] — Add canonical clue library, 6-vote referee, and legacy-safe prevention hooks
**Context:** user wanted duplicate and near-duplicate clue definitions for the same word collapsed into canonical variants, with exact instructions for a migrated Supabase schema, a multi-model referee for same-meaning non-identical definitions, and prevention so future generation/rewrite steps stop re-adding the same idea.
**Happened:** Added canonical clue types and pure helpers in `generator/core/clue_canon_types.py` and `generator/core/clue_canon.py`, plus a Supabase adapter in `generator/core/clue_canon_store.py` that auto-detects schema presence and falls back cleanly when missing. Extended `generator/core/ai_clues.py` with a structured `clue_compare` JSON prompt and a `run_definition_referee()` helper that runs `3` compares on `gpt-oss-20b` and `3` on `eurollm-22b`, with deterministic A/B swapping and aggregated vote results. Added prompt-time prevention by injecting existing canonical definitions into generate/rewrite prompts, and wired canonical resolution into upload/redefine/repair persistence paths while keeping `crossword_clues.definition` materialized for backward compatibility. Added a new offline command `python -m generator.clue_canon backfill ...` with `--dry-run` / `--apply`, hot-word filtering, and disagreement JSONL reporting.
**Verification:** `python3 -m py_compile generator/core/clue_canon_types.py generator/core/clue_canon.py generator/core/clue_canon_store.py generator/core/ai_clues.py generator/clue_canon.py generator/phases/define.py generator/core/rewrite_engine.py generator/phases/upload.py generator/redefine.py generator/repair_puzzles.py generator/core/supabase_ops.py`; `python3 -m pytest tests/test_clue_canon.py tests/test_clue_canon_store.py tests/test_ai_clues.py -q` (`66 passed`); `python3 -m pytest tests/test_rewrite_engine.py tests/test_redefine.py tests/test_repair_puzzles.py -q` (`42 passed`). Also verified migrated schema is visible live via service-role queries. Started a live `python3 -m generator.clue_canon backfill --dry-run --word APA` smoke run, which created the report directory and disagreement file but did not finish within the turn window, so full live backfill output is still pending.
**Outcome:** partial-success
**Insight:** canonical-clue rollout is easiest to keep backward-compatible if canonical ids are resolved only at prompt and DB persistence boundaries; keeping markdown types and worker reads unchanged avoids a wide schema leak while still enabling gradual dedup adoption.
**Promoted:** no

---
