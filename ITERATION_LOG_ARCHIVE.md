# Iteration Log Archive

> append-only. older entries from ITERATION_LOG.md.

### [2026-03-30] Structural rejection persistence, overnight size balancing, centralized Supabase logs

**Context:** persistence of rewrite structural rejection reasons, blind size loop replacement in `run_batch_loop.sh`, generic centralized Supabase update logs.
**Happened:** `RewriteAttemptResult` added (`generator/core/ai_clues.py`). `rewrite_definition()` backward-compatibility + `return_diagnostics=True`. `ClueAssessment` extended (`rewrite_rejection_reason`). `generator/core/rewrite_engine.py` persists last structural rejection on failure. `_synthesize_failure_reason()` updated (prefer verify/rating signal). `generator/core/supabase_ops.py` added `execute_logged_update(...)`. Update sites switched (`activate`, `redefine`, `repair_puzzles`, `retitle`). `generator.loop_controller` added `--auto-size` (live grid counting, missing-size balancing, tie-break). `run_batch_loop.sh` updated.
**Verification:** `python3 -m pytest tests/test_loop_controller.py tests/test_ai_clues.py tests/test_rewrite_engine.py tests/test_batch_publish.py -q` (`120 passed`); `python3 -m py_compile generator/core/ai_clues.py ...`
**Outcome:** success
**Insight:** separate channel for rewrite structural failures. Python controller handles size balancing against live inventory.
**Promoted:** yes

---

### [2026-03-26] Per-round dual title models, shared text cleanup

**Context:** `gpt-oss-20b` empty/weak titles despite high token budget. Objective: title generation like definition generation (low temp, shared cleanup, parallel model candidates).
**Happened:** `generator/phases/theme.py` refactored (query both generators if `multi_model=True`, cross-model rating, best result selection). Title temp lowered `0.9` -> `0.3`. Shared helper `generator/core/llm_text.py` extracted from `ai_clues.py` for plain-text cleanup; reused for titles to strip markdown/labels. Theme tests updated for dual-generator flow.
**Verification:** `python3 -m pytest tests/test_text_rules.py tests/test_theme.py tests/test_retitle.py tests/test_repair_puzzles.py tests/test_batch_publish.py tests/test_ai_clues.py -q` (`148 passed`)
**Outcome:** success
**Insight:** shared cleanup + parallel model generation reduce empty outputs and formatting noise.
**Promoted:** no

---

### [2026-03-26] Title token budget increase for gpt-oss reasoning outputs

**Context:** empty title content rounds in `run_title_improve.sh`. `gpt-oss-20b` filling `reasoning` but leaving `message.content` empty at low budget.
**Happened:** `max_tokens` increased `50` -> `500` in `generator/phases/theme.py`. Title prompts (`generator/prompts/system/theme.md`, `generator/prompts/user/title_generate.md`) synced to `2-5` words per validator rules.
**Verification:** `python3 -m pytest tests/test_theme.py tests/test_retitle.py tests/test_batch_publish.py tests/test_repair_puzzles.py -q` (`88 passed`)
**Outcome:** success
**Insight:** reasoning models consume completion budget on hidden steps; need larger `max_tokens` for short final answers.
**Promoted:** no

---

### [2026-03-26] Deterministic `Fara titlu` for zero-score title generation

**Context:** prevent random fallback labels when title generation fails to beat score 0.
**Happened:** `generator/phases/theme.py` updated: no-signal case returns `Fara titlu`, `score=0`, `used_fallback=True`. Covers all-invalid rounds and low-creativity candidates. Theme tests added for both scenarios.
**Verification:** `python3 -m pytest tests/test_theme.py tests/test_retitle.py tests/test_repair_puzzles.py tests/test_batch_publish.py -q` (`88 passed`)
**Outcome:** success
**Insight:** deterministic labels better than random ones for failed maintenance runs; reduces noise.
**Promoted:** no

---

### [2026-03-25] DEX usage-label suffixes for clues

**Context:** usage/register suffixes (e.g., `(arh.)`, `(reg.)`) for rare/domain-specific words. Requirement: extract from `dex_definitions` text; apply to define/verify/rewrite/rate; rating bias (reward justified, penalize gratuitous).
**Happened:** `generator/core/ai_clues.py` extended with label extraction, suffix normalization, and prompt-context builders. `generate_definition()`/`rewrite_definition()` normalize to one suffix. Validation strips suffixes for gloss checks. Rating scores suffixes asymmetrically. `tests/test_ai_clues.py` expanded. `tests/test_verify.py` updated. `LmRuntime.activate_primary()` mocked in verify tests.
**Verification:** `python3 -m pytest tests/test_ai_clues.py tests/test_verify.py tests/test_rewrite_engine.py -q`; `python3 -m pytest tests/test_redefine.py -q`.
**Outcome:** success
**Insight:** clue validation must strip parenthetical suffixes to prevent masking bad one-word glosses.
**Promoted:** no

---

### [2026-03-25] Redefine metadata refresh per clue update

**Context:** `run_definition_improve.sh` to update metadata/state alongside definitions. Requirement: refresh Supabase metadata, persist `verify_note` + `verified`, backfill missing metadata.
**Happened:** `generator/redefine.py` refactored (baseline re-evaluation vs candidate rewrite loop). `fetch_clues()` loads `clue_number`, `verify_note`, `verified`. Persistence keys by `(direction,start_row,start_col)`. Per-clue metadata refresh: recomputes `description`, scores, `pass_rate` via `puzzle_metrics`. No-op/backfill handling added. `tests/test_redefine.py` expanded.
**Verification:** `python3 -m pytest tests/test_redefine.py`; `python3 -m pytest tests/test_repair_puzzles.py`.
**Outcome:** success
**Insight:** redefine/repair should key clue persistence by coordinates, not `word_normalized`; allows legal duplicates.
**Promoted:** no

---

### [2026-03-24] Normalized Rust engine, pinned Python variant hydration

**Context:** Rust phase-1 engine for normalized grid fill only. Dedupe by normalized word; min rarity for quality; variant resolution outside Rust; fixed variant pinning.
**Happened:** `crossword_engine` refactored: `words.rs` dedupes by normalized key, aggregates min rarity; `engine.rs` returns `EngineError`, removes reuse, monotonic black-dot ladder, first-solution-only, normalized output. `generator/batch_publish.py` groups metadata, randomly pins one concrete variant per clue, rewrites clue originals, injects `word_type`/`word_original` into state. Rust CLI/batch tests updated.
**Verification:** `cargo test`; `pytest tests/test_batch_publish.py -q`; `cargo run ... --bin crossword_phase1`.
**Outcome:** success
**Insight:** normalized-only fill requires immediate variant pinning to prevent downstream metadata randomization.
**Promoted:** yes

---

### [2026-03-23] CI/regression repair after Python phase-1 removal

**Context:** CI failures in `test_ai_clues`/`test_verify` after phase-1 cleanup. Stale `crossword_phase1` processes.
**Happened:** `ENGLISH_HOMOGRAPH_HINTS` restored in `generator/core/quality.py`. `generator/phases/verify.py` updated for optional `model` param. Stale background processes stopped.
**Verification:** `python3 -m pytest tests/test_ai_clues.py tests/test_verify.py -q` (`49 passed`); `python3 -m pytest -q` (`369 passed`)
**Outcome:** success
**Insight:** deletion of "unused" code requires repo-wide consumer search; prompt helpers/tests had dependencies.
**Promoted:** no

---

### [2026-03-23] Legacy Python phase-1 removal

**Context:** remove old Python phase-1 grid generation stack.
**Happened:** Implementation removed from `generator/batch_publish.py`. `_best_candidate()` now Rust-only. Obsolete modules deleted: `constraint_solver.py`, `grid_template.py`, `word_index.py`, `phases/generate_grid.py`, `phases/fill.py`. `generator/core/size_tuning.py` simplified. `generator/rebus.py` hooks removed. `scripts/benchmark_phase1.py` updated for Rust only. `tests/test_constraint_solver.py` etc. deleted.
**Verification:** `py_compile` checks; `pytest tests/test_batch_publish.py tests/test_loop_controller.py -q` (`47 passed`); `cargo test`.
**Outcome:** success
**Insight:** separate shared lexical helpers from implementation code before deletion to avoid test collection failures.
**Promoted:** no

---

### [2026-03-20] results3 archive, 100-experiment campaign redesign

**Context:** forensic review of 150-experiment campaign (99 finished). Objective: new 100-experiment plan with removals/alternation.
**Happened:** `results.tsv` analyzed; `results3_campaign_review.md` written. Campaign archived to `results3.tsv`. `results.tsv` reset. `scripts/run_experiments.py` redesigned (removals first, file alternation). Git commits for results tightened. Runner tests added.
**Outcome:** success
**Insight:** score/prompt history can diverge in live-git campaigns; backups are authoritative for winning state.
**Promoted:** yes

---

### [2026-03-18] Grammatical-form checks, experiment metadata expansion

**Context:** include pruning variants, grammatical checks, readable logs in future experiments.
**Happened:** Base prompts + verify pipeline updated with grammatical category. Form-agreement instructions added to `verify`/`rate`/`rewrite`/`definition`. Experiment definitions updated (removals, grammatical checks). JSON/TSV descriptions enriched. Tests for metadata propagation + description formatting. `DexProvider.for_puzzle()` mocked in `test_verify.py`.
**Outcome:** success
**Insight:** verify/rate unit tests must isolate DEX prefetch for reliability.
**Promoted:** yes

---

### [2026-03-18] 41-experiment campaign recovery after power loss

**Context:** recover prompt edits, discarded results, and logs from interrupted campaign.
**Happened:** `exp001`-`exp041` reconstructed from `run_experiments.py` + `march17_campaign.json`. Reports generated in `build/experiment_reports/`. Discards backfilled to `multistep_results.tsv`. Monolithic log split into `expNNN.log`. `scripts/run_experiments.py` patched (per-experiment logs, discard persistence, edit storage).
**Outcome:** success
**Insight:** power loss can leave prompts ahead of state; diff against campaign backup is mandatory.
**Promoted:** yes

---

### [2026-03-20] results_exp100 stop, results4 archive, top-k verifier implementation

**Context:** stop interrupted campaign, archive `results.tsv`, restore best prompts, implement top-k verification (multiple candidates, any correct = pass).
**Happened:** Campaign stopped; prompts restored to `results_exp100_best` backup. `results.tsv` moved to `results4.tsv`. Top-k verification implemented: `VERIFY_CANDIDATE_COUNT` config added; prompts updated for candidate lists; parsing/storage in `ClueAssessment` + notes. Logic propagated to `verify.py`, `run_assessment.py`, `batch_publish.py`, `redefine.py`, `loop_controller.py`. Tests for parsing/success criteria/difficulty aggregation.
**Outcome:** success
**Insight:** top-k verification requires pipeline-wide adoption (notes, metrics, publication, benchmarks) to be useful.
**Promoted:** yes

---

### [2026-03-18] Multistep benchmark rebuild (March 17), runner hardening

**Context:** replace assessment words with March-17 candidates; multistep benchmark focus; repeatable runs.
**Happened:** March-17 metrics aggregated into low/high TSVs. Dataset builder rewritten (70-word set, short-word caps, DEX reuse). Baseline run on new set (`67.8` composite, `28.6%` pass). `run_experiments.py` patched (live logs, interrupt restoration, discard rollback). History archived; active baseline reset.
**Outcome:** success
**Insight:** discarded experiments must roll back all artifacts (prompts + results) to prevent hill-climbing poisoning.
**Promoted:** yes

---

### [2026-03-14] Prefix stripping, forbidden stems in family check

**Context:** TIBETAN (Tibet) and NEINCEPUT-type family check failures.
**Happened:** Romanian prefix stripping added to `clue_family.py`. `forbidden_definition_stems()` + `_family_exclusion_note()` added to prompts. OU/URINARE presets removed.
**Outcome:** pending
**Insight:** prefix stripping + forbidden stems essential for Romanian morphological family checks.
**Promoted:** yes

---

### [2026-03-21] Rewrite/failure flows top-k verifier integration

**Context:** ensure top-3 verification is respected in generation/evaluation/rewrite flows.
**Happened:** Audit confirmed pass/fail/selection/metrics already using `verify_candidates`. Gap fixed: rewrite prompts/failure reasons updated to use candidate lists (`Rezolvitorul a propus: ...`). `generator/core/ai_clues.py` (`_synthesize_failure_reason()`) updated. `batch_publish.py`/`redefine.py` pass candidate lists to rewrite. Regression tests for prompt rendering.
**Outcome:** success
**Insight:** `wrong_guess` is lossy; use `verify_candidates` as primary signal for rewrite decision-making.
**Promoted:** yes

---

### [2026-03-21] Assessment dataset DEX alignment

**Context:** ensure expanded DEX provider context reaches assessment datasets, not just live generation.
**Happened:** DEX call sites audited. Live paths confirmed. `generator/assessment/prepare_dataset.py` gap fixed: `_reuse_or_fetch_dex()` now prefers live provider `lookup()` over stale `dataset.json` strings. Regression test for refresh logic.
**Outcome:** success
**Insight:** materialized datasets must refresh context from live providers to avoid silent divergence from production.
**Promoted:** yes

---

### [2026-03-21] DEX semantic-base extraction for short definitions

**Context:** extend semantic context beyond redirect formulas for short (<10 word) first definitions.
**Happened:** Expansion patterns added to `generator/core/dex_cache.py`: synonym glosses, `Acțiunea de a (se) X`, `Faptul de a (se) X`, `Proprietatea de a fi X`, `A <ordinal> parte dintr-un/dintr-o X`. Target cleanup (strip punctuation/sense markers). Trigger limited to first parsed definition. Unit tests for families + fractions.
**Outcome:** success
**Insight:** first parsed definition is the reliable trigger for structural semantic expansion.
**Promoted:** yes

---

### [2026-03-21] Local disk DEX cache layer (gitignored)

**Context:** reduce Supabase/network chatter via local disk cache.
**Happened:** `DexProvider` expanded to 4-layer cache: memory -> disk -> Supabase -> dexonline. `.cache/dex_definitions` directory (gitignored) stores JSON entries (status, html, original, fetched_at). Wired into `get()`/`lookup()`/`prefetch()`/redirects. Negative results cached. Tests for priority/negative cache/persistence.
**Outcome:** success
**Insight:** local disk cache must precede Supabase for all lookup types, including redirect dereferencing.
**Promoted:** yes

---

### [2026-03-20] DEX redirect parsing, one-hop semantic expansion

**Context:** parser bugs and semantic thinness in `FIRISOR`-type redirect definitions (HTML tags causing failures).
**Happened:** `_DefinitionExtractor` audited/fixed (inline tag depth). Redirect/meta-pattern detection added. 1-hop dereference to base lexeme; `Sens bază pentru ...` injection. `uncertain_short_definitions()` collection + logging. Tests for markup/expansion/short-entries.
**Outcome:** success
**Insight:** redirect-style entries require both parser robustness and bounded dereference expansion.
**Promoted:** yes

---

### [2026-03-20] Baseline validation, smoke artifact audit, gap closure

**Context:** validate new baseline on real artifacts; close objective-alignment bugs.
**Happened:** Baseline confirmed (`65.0`). Smoke runs exposed: missing `model_generated` provenance; markdown emphasis leaks; `verified=False` clues escaping rewrite due to rarity override. Fixes: added tests, updated rarity logic. Coherence bug: `clue.locked` logic fixed (requires `verified=True`). `_is_publishable()` floor added (0.5 pass rate). `rate_definition()` retry hardening. Validation for one-word glosses/dangling endings added. Romanian-only title enforcement. Rewrite churn metrics instrumented.
**Outcome:** success
**Insight:** exact-solve alignment requires consistency across rewrite gating, locking, and publication thresholds.
**Promoted:** yes

---

### [2026-03-20] Generator correctness, objective-alignment fixes

**Context:** fix `_best_candidate()` early return, LM Studio model switching bugs, and residue in `defs.md`.
**Happened:** `_best_candidate()` fixed. LM Studio model switching logic corrected (key vs instance id). Clean `defs.md` export (score removal). Clue selection updated for exact verification weight. Richer word-difficulty metrics added. Tests for model switching/selection/export. Task list created in `build/experiment_reports/`.
**Outcome:** success
**Insight:** selection alignment and model unloading are critical for objective alignment.
**Promoted:** yes

---

### [2026-03-28] Side-effect-free prompt autoresearch inspection

**Context:** prevent `--dry-run`/`--status` from rebuilding durable state; fix stale manifest-anchor test.
**Happened:** `scripts/prompt_autoresearch.py` updated (side-effect-free inspection reads existing state; temporary bootstrap only if no state). Manifest-anchor test in `tests/test_run_experiments.py` updated for `v2`/`v3` manifests. Manual `v3` runbook added.
**Verification:** `.venv/bin/python -m pytest tests/test_run_experiments.py ...` (`60 passed`)
**Outcome:** success
**Insight:** inspection commands must be read-only; maintenance paths need separate codepaths from mutable state machine.
**Promoted:** no

---

### [2026-03-28] Assessment ledger rotation (results6)

**Context:** preserve `results.tsv` history before fresh baseline; prevent overwrite.
**Happened:** `results.tsv` copied to `results6.tsv`. `results.tsv` reset to header. Code refs still target `results.tsv`.
**Outcome:** success
**Insight:** benchmark rotation is ledger archiving while keeping the canonical filename empty for the next run.
**Promoted:** no

---

### [2026-03-28] v4 rewrite-focused batch preparation, results7 rotation

**Context:** `v3exp016` confirmed win. Prepare `v4` experiment set (rewrite prompt focus); archive ledger.
**Happened:** `v4` experiment namespace added (explicit rule re-additions, header variants, compactness bias). 8 single-file edits targeting `rewrite.md`. `v4` run commands added to docs. `results.tsv` archived to `results7.tsv`; reset to header.
**Outcome:** success
**Insight:** winning cleanup experiments should be followed by narrow probes to isolate redundant vs necessary constraints.
**Promoted:** no

---

### [2026-03-22] Durable prompt-autoresearch supervisor, pilot ledger reclassification

**Context:** fix `uncertain` semantics; recoverable overnight loop (no API dependency); update pilot ledger.
**Happened:** `benchmark_policy.py` updated (near-miss/family-stopping constants). `run_experiments.py` refactored (metadata, gain/loss summaries, structured decision returning `keep/uncertain/discard`). `scripts/prompt_autoresearch.py` supervisor added (snapshot storage, campaign replay, reclassification against baseline JSON, family tracking). State bootstrapped from `pilot_20260321.json` + baseline; reclassified pilot rows (kept `exp001`/`exp002`, discarded others). Tests for staleness/unlocking/recovery.
**Outcome:** success
**Insight:** overnight optimization must be a resumable state machine over snapshots, not a loose chat/manifest.
**Promoted:** yes

---

### [2026-03-21] Shared runtime logging, rewrite engine, structured assessment

**Context:** implement operational refactor: centralized logging, DEX short audit, shared rewrite, aligned assessment.
**Happened:** `generator/core/runtime_logging.py` added (timestamped logs, UTC persistence, JSONL audit). Wired into all CLI/scripts. DEX short-definition audit events added to `generator/core/rewrite_engine.py`. `generator/core/model_session.py` added (session-based orchestration). `redefine.py`/`batch_publish.py` migrated to shared rewrite engine. `run_assessment.py` emits machine-readable JSON artifact. `scripts/run_experiments.py` updated (artifact consumption, classification, summary persistence).
**Outcome:** success
**Insight:** centralizing runtime concerns (logging, sessions) is the fastest way to stabilize a complex pipeline before policy changes.
**Promoted:** yes

---

### [2026-03-21] Curated 20260321 benchmark reset, 100-experiment manifest

**Context:** replace March assessment set with curated 70-word set; implement 100-experiment manifest (including bundles).
**Happened:** `prepare_dataset.py` patched (tier map: 30 low/25 medium/15 high; live DEX refresh). `dataset.json` regenerated. `scripts/run_experiments.py` updated (multi-file experiments, atomic edits, manifest validation, sys.path bootstrap). New manifest: cleanup (12), verify (24), rewrite (12), definition (12), rate (12), bundles (26). Anchor-existence regression test added.
**Verification:** `dry-run` pass; anchor tests pass.
**Promoted:** yes

---

### [2026-03-22] Post-exp053 prompt autoresearch analysis

**Context:** analyze trials post-`exp053` (repeated patterns, gains/regressions, family justification).
**Happened:** `build/prompt_research/` audited (log, events, state, families, trial artifacts). 8 straight discards post-`exp053`. Stale families: `definition_positive_examples`, `definition_guidance`, `rate_rules`. `EPIGASTRU` gained; `ETAN`/`OSTRACA`/`SAN` regressed. Next candidate: `rewrite_structural_guidance` (`exp040`).
**Outcome:** success
**Insight:** none promoted (analysis only).

---

### [2026-03-21] Pilot-first benchmark workflow (baseline_results_20260321)

**Context:** lock March 21 benchmark as target; pilot run first; priorities in code; fragile-word handling (`ADAPOST`/`ETAN`).
**Happened:** March 21 reset structure adopted. `results.tsv` used as incumbent truth via `load_latest_kept_result()`. Runner presets added (pilot, cleanup, etc.). `--end-at` and `--summarize-log` added. Control-word watch logic for `ADAPOST`/`ETAN`. `--control-baseline-json` support. Step commits made.
**Outcome:** success
**Insight:** incumbent scores belong in ledger; stability policy requires full assessment JSON.
**Promoted:** yes

---

### [2026-03-23] Runner cache migration (best_assessment.json)

**Context:** move `best_assessment.json` from tracked prompt source to untracked `build/`.
**Happened:** `scripts/run_experiments.py` refactored: `best_result_summary_path()` now under `build/prompt_experiment_state/`. Legacy fallback loading preserved. Tests for new path + fallback.
**Outcome:** success
**Insight:** cache/benchmark artifacts must be outside source roots to prevent accidental commits.
**Promoted:** yes

---

### [2026-03-23] v2 prompt autoresearch expansion (40 trials), rebuild fix

**Context:** next v2 batch (40 experiments); narrow hypothesis focus. fix `--rebuild-state` side effects.
**Happened:** v2 reshaped around 4 families: `short_word_exactness`, `near_neighbor`, `blank_output`, `technical_noun`. `scripts/run_experiments.py` expanded to 40 trials; v2 presets added; campaign-stop helper. `benchmark_policy.py` updated (v2 thresholds). `scripts/prompt_autoresearch.py` fixed: `main()` returns after rebuild (no auto-launch). `audit()` creates parent dir lazily. `build/prompt_research_v2/` rebuilt.
**Verification:** tests pass; status check valid.
**Outcome:** success
**Insight:** maintenance commands must be side-effect free; avoid accidental benchmark launches.
**Promoted:** yes

---

### [2026-03-23] v3 mixed prompt+system lane, model plumbing, integrity checks

**Context:** mixed `prompt + system` batch (temperature + rewrite). explicit model plumbing (remove LM Studio default routing dependence).
**Happened:** `scripts/run_experiments.py` fixed (missing `v2_exp` helpers). Logic errors in variant selection and autoresearch rebuilds fixed. Explicit `model` passthrough added to `generate`/`rewrite`/`verify`/`rate` and assessment. `--generate-temperature`/`--rewrite-temperature` support. v3 manifest: temp (4), verify (4), rewrite (4), dedup (4). Tests for model passthrough and v3 overrides.
**Outcome:** success
**Insight:** supervisor rebuilds must restore live prompt tree and swap snapshot paths to ensure durability.
**Promoted:** yes

---

### [2026-03-22] Mobile rebus scroll-jump, pen-mode clarity

**Context:** fix mobile tap auto-scroll jump; clarify pen mode UI.
**Happened:** `frontend/src/components/clue-panel.ts` auto-scroll gated (only if container is scrollable). `focus({ preventScroll: true })` used. Pencil button: explicit `Creion` label, `aria-pressed`, distinct off-state. `pencil-help.ts` modal (persisted `rebus_pencil_help_seen`). `npm run build` pass.
**Outcome:** success
**Insight:** mobile auto-scroll must be gated by internal containers to prevent full-page jitter.
**Promoted:** yes

---

### [2026-03-23] overnight 15x15 loop inclusion, search retuning

**Context:** include `15x15` in `run_batch_loop.sh`; tune search budget to ~1 min.
**Happened:** `size_tuning.py` defaults updated (`7..15`). `min_preparation_attempts` lowered to `1` for `15x15`. Rust `attempt_budget` tuned (`50`); `max_nodes=5M`.
**Verification:** 15x15 release probe: `65.55s` pass.
**Outcome:** success
**Insight:** native search loop speedups require shrinking legacy pipeline retry floors.
**Promoted:** no

---

### [2026-03-23] Rust phase-1 integration, rarity removal from search

**Context:** move grid generation to Rust binary (speedup). Requirements: `run_batch_loop.sh` entrypoint; unchanged downstream flow; rarity-free search.
**Happened:** Rust crate `crossword_engine/` added (DFS solver, MRV/forward checking, JSON stdout). `run_batch_loop.sh` builds release binary. `batch_publish.py` shells out to Rust; difficulty computed without rarity. Legacy Python path kept as `_best_candidate_python()`.
**Verification:** speedup `59x` on 7x7. tests pass.
**Outcome:** success
**Insight:** native migrations succeed by preserving host-language contracts and swapping implementation behind thin wrappers.
**Promoted:** yes

---

### [2026-03-22] prompt-manifest anchor check hardening

**Context:** fix CI failures when baseline cleaning pre-emptively lands manifest replacements.
**Happened:** `apply_experiment()` updated: `edit.replace` already present = clean skip. Anchor-regression test updated to accept current `find` or `replace` state. Unit tests for skip logic.
**Outcome:** success
**Insight:** anchor checks should enforce semantic applicability, not literal wording; accept already-landed state.
**Promoted:** yes

---

### [2026-03-22] prompt autoresearch incumbent persistence, atomic rebuilds

**Context:** fix durable state drift (state.json vs incumbent.json); safe resume logic.
**Happened:** `scripts/prompt_autoresearch.py` refactored (explicit bootstrap/resume/rebuild). `persist_campaign_state()` only write helper. `rebuild_state_from_campaign()` uses temp dir + swap. absolute snapshot paths refreshed after move. `--continuous` mode. tests for mismatch/preservation.
**Outcome:** success
**Insight:** durable-state repairs must be atomic; rebuild in temp dir to prevent state destruction.
**Promoted:** yes

---

### [2026-03-24] Rust size formula, dictionary-length pressure

**Context:** replace hardcoded size table with formulaic scaling; check if long-word scarcity drives failures.
**Happened:** Rust size table replaced by formulas (density, budgets, tolerance). Dictionary-length pressure: nudge budgets based on `5..8` vs long-word bucket scarcity. Tests for monotonic settings. 15x15 release probe.
**Outcome:** partial (15x15 still bottlenecked by topology)
**Insight:** search settings should reflect dictionary histograms, not board dimensions alone.
**Promoted:** yes

---

### [2026-03-26] Redefine/retitle logging, oldest-first maintenance

**Context:** disk logs for `redefine`/`retitle`; sort by oldest puzzle first.
**Happened:** Timestamped artifact dirs added: `redefine_runs/`, `retitle_runs/`. Fetch ordering: `created_at` ASC, `id` tie-break. tests for sorting + file creation.
**Verification:** `pytest` pass.
**Outcome:** success
**Insight:** maintenance jobs require explicit artifact paths and row ordering to survive terminal sessions.
**Promoted:** no

---

### [2026-03-26] Retitle duplicate-name puzzles, unique normalized titles

**Context:** prioritize duplicate titles; unique normalized titles globally; bump `updated_at`.
**Happened:** `normalize_title_key()` helper (whitespace/punctuation/diacritic collapse). `generator/retitle.py` selects duplicate clusters (descending size). Duplicate rejection during retries via live title-key set. `title` + `updated_at` updated.
**Outcome:** success
**Insight:** title dedup must be run-stateful; check against current batch progress, not just DB snapshot.
**Promoted:** no

---

### [2026-03-26] DEX usage-category heading preservation

**Context:** extract register/rarity info from dexonline headings like `Arhaisme și regionalisme`.
**Happened:** HTML parser in `generator/core/dex_cache.py` extended to read usage callout headings. Category-tagged entries (e.g., `Arhaisme...: ...`) appended to parsed text. Non-usage categories (e.g., `Sinonime`) excluded.
**Outcome:** success
**Insight:** register metadata often lives in section headers outside the definition span.
**Promoted:** no

---

### [2026-03-26] Prioritize never-repaired puzzles in redefine

**Context:** `run_definition_improve.sh` starting with fresh work while old rows have null rebus/metadata.
**Happened:** `generator/redefine.py` sorting: `repaired_at IS NULL` first, then missing metadata, then age. Regression tests for priority buckets.
**Outcome:** success
**Insight:** `repaired_at` must dominate `created_at` in maintenance queues to prevent starvation of old/null rows.
**Promoted:** no

---

### [2026-03-22] Prompt autoresearch family regrouping

**Context:** stop stale-family threshold from killing unrelated hypothesis classes in coarse manifests.
**Happened:** families split: `rewrite_anti_distractor` -> `framing`/`structural`; `definition_examples` -> `negative`/`positive`/`guidance`; `rate_exactness` -> `counterexamples`/`rules`. bundle unlocks updated. durable state rebuilt.
**Outcome:** success
**Insight:** stale-stop logic requires families mapped to hypothesis classes, not manifest chunks.
**Promoted:** yes

---

### [2026-03-26] Title validation/backfill in retitle flow

**Context:** structural validation for old titles before rerating; backfill missing `title_score`.
**Happened:** Old titles structural gate (ALL CAPS, mixed language, etc.). Invalid old titles assigned `old_score = 0` (no LLM call). Missing `title_score` backfilled on skip (kept old title). Single final update on win. Tests for invalid titles/backfill.
**Outcome:** success
**Insight:** maintenance must validate legacy content with current rules before comparison to prevent legitimizing corrupt data.
**Promoted:** no

---

### [2026-03-26] Retitle batch processing, reduced model activation

**Context:** minimize LM Studio load/unload thrash; batch title generation in `retitle`.
**Happened:** Redundant reactivation removed from `theme.py`. `retitle.py` batch runner: 10 puzzles per batch; GPT generate -> switch -> Euro rate/generate -> switch -> Euro rate. Initial publish path remains single-puzzle for stability.
**Outcome:** success
**Insight:** batching orchestration belongs at the batch-owner level (retitle), not the generic generation primitive.
**Promoted:** no

---

### [2026-03-26] `retitle --all` semantics fix

**Context:** `./run_title_improve.sh` to process all puzzles, prioritizing missing scores.
**Happened:** `select_puzzles_for_retitle(...)` now orders by `(missing title_score, created_at, id)`. Duplicate-only filtering moved to `--duplicates-only`. `run_title_improve.sh` now processes the full table as implied.
**Outcome:** success
**Insight:** `--all` flags should not implement subset filtering; explicit modes are better.
**Promoted:** no

---

### [2026-03-26] Title dual-generator repair, per-model hints

**Context:** fix orchestration/truthfulness in dual-model title generation; correct noisy retries.
**Happened:** `generator/phases/theme.py` generator loop: activation just-in-time before call; evaluator activation after validation. Per-model rejected-history: corrective hints (e.g., `max 5 cuvinte`) injected after specific model failure. Empty outputs trigger isolated retry. Romanian-only 2-5 word constraint enforcement.
**Outcome:** success
**Insight:** just-in-time activation prevents log falsification and identifies true source of failure (prompt vs model).
**Promoted:** yes

---

### [2026-03-28] content-based progress tracking

**Context:** only mark puzzles `in progress` after letters are filled.
**Happened:** `hasFilledCells()` helper added to `progress-storage.ts`. `main.ts`: clear empty snapshots; list view filters by non-empty progress.
**Outcome:** success
**Insight:** resume semantics need a content threshold to avoid noise from navigation side effects.
**Promoted:** no

---

### [2026-03-28] mobile selector disclosure pattern

**Context:** compact frontend filter area on mobile.
**Happened:** `puzzle-selector.ts` default: `Filtre (n)` disclosure + short sort dropdown. Controls (status/size/hide) moved to expandable panel. `gamification.css`/`responsive.css` updated.
**Outcome:** success
**Insight:** mobile filters should use disclosure + summary to save vertical space.
**Promoted:** no

---

### [2026-03-28] Frontend discovery refresh, local challenges

**Context:** scale past 300 puzzles; size-first browsing; list state preservation; local challenges.
**Happened:** `main.ts` list derivation with explicit browse state. `puzzle-selector.ts` refactored (chips, reset, empty state, cards). `challenges.ts` (local challenge derivation from progress). `checksUsed` support in storage. Scroll/state preservation on list return. `stats-panel.ts` reframed as progress view (points, solved, badges).
**Outcome:** success
**Insight:** none (UI polish).
**Promoted:** no

---

### [2026-03-23] hybrid de-anchoring for redefine/repair (no prompt changes)

**Context:** reduce bad clue anchoring bias without prompt edits.
**Happened:** `generator/core/rewrite_engine.py` added `hybrid_deanchor` (round 1 only). Generates two candidates: `rewrite` vs `fresh_generate`. Best candidate (verified/rated) selected. Enabled in `redefine`/`repair`. Tests for branching logic.
**Outcome:** success
**Insight:** de-anchoring achievable via candidate branching through existing verifiers/raters.
**Promoted:** no

---

### [2026-03-23] Repair pipeline with score backfill, dual timestamps

**Context:** repair published puzzles (missing scores first, then low-score). regenerate only if rebus score improves.
**Happened:** DB schema/migration (`updated_at`/`repaired_at`, scores). `generator/core/puzzle_metrics.py` (shared scoring). `generator/repair_puzzles.py`: `min_rebus` acceptance gate; published-only queue. Worker/frontend updated for timestamps and sorting. fixed `H`/`V` direction adapter.
**Outcome:** success
**Insight:** treat compact persisted enums (H/V) as primary in DB adapters.
**Promoted:** yes

---

### [2026-03-23] v1 archive, fragile-word guards, v2 bootstrap

**Context:** freeze v1; save `results5.tsv`; exp002 seed; fragile-word evaluation guards; start v2 (12 trials).
**Happened:** `benchmark_policy.py` added fragile-word watchlists. `run_experiments.py`: experiment namespaces; fragile-word loss = discard. `results.tsv` archived to `results5.tsv`; reset with exp002 seed. v2 bootstrapped in `build/prompt_research_v2/`.
**Outcome:** success
**Insight:** move known loser clusters into live classifier and stop policy before next campaign.
**Promoted:** yes

---

### [2026-03-26] title score persistence, structural screening

**Context:** persist `title_score`; structural rejects (ALL CAPS, 6+ words, solution overlap >2 chars).
**Happened:** `text_rules.py` normalization helpers. `theme.py` screening vs fallback selection. `TITLE_MIN_CREATIVITY=8`. Overlap check (`min_length=3`). `title_score` propagated to `batch_publish`, `redefine`, `repair`. Local schema/docs updated.
**Outcome:** success
**Insight:** structural screening must precede scoring to keep score metadata meaningful.
**Promoted:** yes

---

### [2026-03-29] Policy update: baseline_results_20260329_v4exp001

**Context:** point `WORKING_BASELINE_DESCRIPTION` at fresh `v4exp001` baseline.
**Happened:** `benchmark_policy.py` updated. tests synced.
**Outcome:** success
**Insight:** update incumbent labels immediately after fresh baseline recording to prevent manual/automated disagreement.
**Promoted:** no

---

### [2026-03-29] Phase-specific GPT-OSS reasoning profiles

**Context:** reusable request-time reasoning effort (LM Studio capabilities).
**Happened:** `gpt-oss`: `medium` effort for generate/rewrite/rate; `low` for verify/tiebreak/title. `max_tokens=2000` (normalized). `reasoning_tokens` unset (LM Studio stability).
**Outcome:** success
**Insight:** phase-level granularity (creative vs deterministic) is the useful reasoning control.
**Promoted:** no

---

### [2026-03-29] `reasoning_effort=low` default for GPT-OSS

**Context:** LM Studio `/v1/chat/completions` support for `reasoning_effort`.
**Happened:** `model_manager.py` expanded with `reasoning_effort`. `PRIMARY_MODEL` set to `low`. Shared helper in `ai_clues.py` routes all chat calls. Payload tests added.
**Outcome:** success
**Insight:** static config + shared helper is safest for early/unreliable capability metadata.
**Promoted:** no

---

### [2026-03-29] v4exp001 confirmation, v5 opening, ledger rotation

**Context:** `v4exp001` confirmed (75.9 composite). prep `v5` (framing signal isolation); archive results.
**Happened:** `v4exp001` kept. `v5` manifest added (8 rewrite probes: headers, precision lines). `results.tsv` archived to `results8.tsv`; reset to header.
**Outcome:** success
**Insight:** record one dedicated baseline for adopted prompt before next batch; archive confirmation noise.
**Promoted:** no

---

### [2026-03-28] Selection bias fix, tier-balanced pass metric

**Context:** fix undercounting when pass1/pass2 identical; add tier-balanced pass metric.
**Happened:** `selection_engine.py`: `choose_clue_version()` prefers verified variant for equivalent text. `tier_balanced_pass_rate` (mean of per-tier rates) added to `run_assessment.py`.
**Outcome:** success
**Insight:** assessment needs tier-balanced metrics to prevent large tiers from dominating interpretation.
**Promoted:** no

---

### [2026-03-29] Benchmark regime reset, assessment DEX refresh, v6 opening

**Context:** reset benchmark around replicated evidence; fresh DEX text; v6 verify/rate/definition focus.
**Happened:** `run_assessment.py` refreshes `dex_definitions` before fallback. `run_experiments.py` uses replicated comparison (`--comparison-runs=3`); machine-readable summaries. `tier_balanced_pass_rate` in keep/discard logic. `v6` (8 experiments) added.
**Outcome:** success
**Insight:** once semantics drift, replace ledger highs with replicated machine-readable comparisons and fresh context.
**Promoted:** yes

---

### [2026-03-28] v4 rewrite reframe: positive phrasing

**Context:** avoid negative banned-token phrasing (e.g., "don't use X") for local models to prevent anchoring.
**Happened:** `v4` manifest rewritten (positive Romanian-register, referent-first, lexical-distance phrasing). `prompt_research.md` hypothesis updated.
**Outcome:** success
**Insight:** prefer positive target-state phrasing over negative bans to avoid forbidden-token anchoring.
**Promoted:** no

---

### [2026-03-28] v3exp016 promotion to baseline

**Context:** confirmed `v3exp016` rewrite win. align benchmark policy.
**Happened:** `results.tsv` archived; fresh baseline `baseline_results_20260328_v16` recorded. `benchmark_policy.py` updated. tests synced.
**Outcome:** success
**Insight:** update incumbent policy immediately after fresh baseline to keep automation aligned.
**Promoted:** no

---

### [2026-03-30] rewrite/rate budget increase for LM Studio reasoning

**Context:** mass failures (`too short`, `JSON invalid`) after `reasoning_effort` active.
**Happened:** `rewrite_definition()`/`rate_definition()` `max_tokens` raised to `2000`. Truncation logging added (`finish_reason="length"`). `tests/test_ai_clues.py` updated.
**Outcome:** success
**Insight:** reasoning tokens consume same completion budget; budget retuning mandatory after enabling reasoning.
**Promoted:** yes

---

### [2026-03-31] Bold crossword grid letters

**Context:** stronger reading of grid letters.
**Happened:** `frontend/src/styles/grid.css`: `.cell__input` `font-weight: 700`.
**Outcome:** success
**Insight:** crossword cells need high contrast vs borders/numbers.
**Promoted:** no

---

### [2026-03-31] Stable definition bar, auto-shrink clue text

**Context:** fixed definition bar height; shrink long clues; remove jump; hide `16/44` counter.
**Happened:** `gamification.css`: 3-line height for definition bar. `definition-bar.ts`: font-fit loop (shrink-to-fit clue text). `grid.css`: `progress-counter` hidden. `main.ts`: `resize` hook for re-fitting.
**Outcome:** success
**Insight:** stabilize container height before fitting typography to prevent vertical layout jitter.
**Promoted:** no

---

### [2026-03-31] Crossword backspace retreat behavior

**Context:** virtual/physical `Backspace` moves to previous square.
**Happened:** `input-handler.ts`: `Backspace` clears current/moves back (if content exists) or moves back/clears previous (if empty). `main.ts`: touch remote wired to same helper.
**Outcome:** success
**Insight:** virtual keyboards should match physical keyboard backtracking/editing flow.
**Promoted:** no

---

### [2026-03-31] Agent memory/config maintenance pass

**Context:** periodic audit of `AGENTS.md`, `CLAUDE.md`, etc.
**Happened:** root files + `.claude/agents/` audited. `SETUP_AI_AGENT_CONFIG.md` added to fix broken reference in `AGENTS.md`. No growth/cleanup needed elsewhere.
**Outcome:** success
**Insight:** prioritize structural integrity (broken references) over micro-pruning in maintenance.
**Promoted:** no

---

### [2026-03-31] Upload timestamps, backfill wrapper, mobile pencil emoji

**Context:** `updated_at` on upload; repo-root backfill wrapper; mobile pencil emoji.
**Happened:** `upload.py`: `created_at` + `updated_at` set (UTC ISO). `run_clue_canon_backfill.sh` wrapper added. `upload_phase.py` tests added. `README.md` examples. `frontend/index.html`: `✏️` icon added to toolbar.
**Outcome:** success
**Insight:** wrap canonical commands for operator-facing entrypoints to avoid orchestration drift.
**Promoted:** no

---

### [2026-03-31] Touch-only crossword remote, synced direction control

**Context:** suppress OS keyboard on phone/tablet; QWERTY remote; synced direction icon.
**Happened:** `index.html`: `touch-remote` block (QWERTY, direction button). `GridState`: `touchRemoteEnabled`; grid inputs `readOnly`. `main.ts`: remote rerenders on `refresh()` (icon reflects `activeDirection`). toolbar compressed.
**Outcome:** success
**Insight:** route virtual keyboard through same state transitions as physical input for stable gameplay logic.
**Promoted:** no

---

### [2026-03-30] Solved-view full solution hydration

**Context:** solved puzzles show empty grids on reopen.
**Happened:** `main.ts`: solved-view hydrates `cells` from `/solution`; marks revealed; disables actions. `GridState`: `isSolvedView` flag. `grid.css`: readonly styling.
**Outcome:** success
**Insight:** solved-history reopen must explicitly hydrate visible cell state.
**Promoted:** no

---

### [2026-03-31] Canonical clue library, referee, prevention hooks

**Context:** deduplicate same-meaning clue definitions; 6-vote multi-model referee; prevention hooks for generation.
**Happened:** `clue_canon.py`/`clue_canon_store.py` (Supabase adapter + types). `run_definition_referee()` added (3x GPT-OSS, 3x EuroLLM). canonical definitions injected into generation prompts. upload/redefine paths use canonical resolution. `python -m generator.clue_canon backfill` offline command added.
**Verification:** tests pass (66). referee logic verified.
**Outcome:** success
**Insight:** keep dedup backward-compatible by resolving canonical ids only at prompt/persistence boundaries.
**Promoted:** no
