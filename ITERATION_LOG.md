# Iteration Log

> append-only. entry end every iteration.
> same issue 2+ times? → promote to `LESSONS_LEARNED.md`.

## Entry Format

---

### [YYYY-MM-DD] Brief Description

**Context:** goal / trigger
**Happened:** key actions, decisions
**Outcome:** success / partial / failure
**Insight:** (optional) what next agent
**Promoted:** yes / no

---

### [2026-04-13] Fix LLM client streaming suppression + mock robustness

**Context:** implement Logging & Scheduling Optimization plan + tests green.
**Happened:** Audit `llm_client.py` + test failures. `test_llm_debug.py` fail: `_create_chat_completion_once` bypass streaming when debug off. Fake client demand streaming. Fix: always try streaming for consistency + reasoning. Harden `_chat_completion_create_streaming` for `delta` (real) + `message` (mock) to avoid breaking 17 tests. Internalize debug suppression in `_DebugStreamChannel`. Verify heartbeat, `failures.log`, `resolve_canonicals` active.
**Outcome:** success
**Insight:** Consistent streaming capture reasoning; require chunk-parser robustness for legacy mocks lacking `delta`.
**Promoted:** yes

---

### [2026-04-09] Stabilize `run_all` deterministic stalls + fail fast

**Context:** `run_all.sh` stall root cause + implementation to prevent repeat.
**Happened:** Audit `generator/output/run_all_runs/20260407_132542/run.log`; confirm partial outage. Fix pair-finalization crash in `generator/phases/verify.py`; remove missing second-model vote index. Refactor `generator/supervisor/jobs/simplify.py`; split survivor handling to `plan_survivors -> rewrite_secondary -> validate_primary -> apply_merge`. Add supervisor failure memory + deterministic quarantine in `generator/supervisor/scheduler.py` / `generator/supervisor/types.py`. Add heartbeat summaries + generate-size cooldown/penalty in `generator/loop_controller.py` / `generator/supervisor/pollers.py`. Extend tests.
**Verification:** `python3 -m py_compile generator/phases/verify.py generator/supervisor/types.py generator/supervisor/scheduler.py generator/supervisor/pollers.py generator/supervisor/jobs/simplify.py generator/loop_controller.py tests/test_verify.py tests/test_run_all.py tests/test_loop_controller.py`; `python3 -m pytest tests/test_verify.py tests/test_run_all.py tests/test_loop_controller.py -q` (52 passed); `python3 -m pytest tests/test_clue_canon_simplify.py tests/test_llm_dispatch_enforcement.py -q` (15 passed).
**Outcome:** success
**Insight:** Unattended orchestration bugs need crash fix + supervisor policy to prevent repeat.
**Promoted:** yes

---

### [2026-04-03] Convert repo-wide Python progress prints to shared runtime logging

**Context:** verbose timestamped logs + repo-wide conversion of `print()` to standard logger; keep implementation dry.
**Happened:** Audit Python sources with `rg`; find raw `print()` in jobs, scripts, runners. Replace human-facing progress output with `generator.core.runtime_logging.log()` across assessment, batch publish, retitle/redefine/repair, define/verify/theme/upload/download/activate, rewrite engine, prompt-autoresearch, experiment scripts. Reuse `install_process_logging()`/`log()` stack. Keep raw stdout only in benchmark row emitter + logger primitive. Update `tests/test_runtime_logging.py` to assert via `log()`.
**Verification:** `python3 -m py_compile ...` (list of files); `python3 -m pytest tests/test_runtime_logging.py tests/test_ai_clues.py tests/test_clue_canon.py tests/test_clue_canon_store.py tests/test_model_session.py tests/test_redefine.py tests/test_repair_puzzles.py tests/test_upload_phase.py -q` (121 passed); final `rg -n "\\bprint\\("` returns only `generator/core/runtime_logging.py`.
**Outcome:** success
**Insight:** standardize on single tee logger primitive; reserve raw stdout for data-export scripts.
**Promoted:** yes

---

### [2026-04-03] Make clue-canon backfill resumable, singleton-complete, and cutover-safe

**Context:** `run_clue_canon_backfill.sh` stop hang / babysitting; migrate singleton clue rows; make canonical clue storage permanent.
**Happened:** Refactor `generator/clue_canon.py` to streaming per-word commits + resumable JSON checkpoint `build/clue_canon/`. Process all clue rows (verified + unverified); include singletons; persist canonical pointer per row; write disagreement/quarantine reports incrementally. Add backfill-only adaptive referee voting in `generator/core/ai_clues.py`; stop before full budget for obvious non-matches / unanimous same-sense pairs. Harden upload/redefine/repair writers to fail without `canonical_definition_id`. Add `python -m generator.clue_canon audit` to block cutover if null canonical refs / legacy fallback / legacy-column references exist. Update final SQL migration to assert zero null canonical ids.
**Verification:** `python3 -m py_compile ...` (list of files); `python3 -m pytest tests/test_ai_clues.py tests/test_clue_canon.py tests/test_clue_canon_store.py tests/test_model_session.py -q` (81 passed); `python3 -m pytest tests/test_redefine.py tests/test_repair_puzzles.py -q` (33 passed); `python3 -m pytest tests/test_upload_phase.py -q` (1 passed).
**Outcome:** success
**Insight:** enforce migration coverage, live writer guarantees, + legacy-read detection via audit command before destructive SQL step.
**Promoted:** yes

---

### [2026-04-01] Decouple clue flows from `crossword_clues.definition` and standardize clue/canonical logs

**Context:** safe legacy column drop after canonical migration; clearer logging with maintainable shared formatting.
**Happened:** Add clue-definition compatibility to `ClueCanonStore`: schema probing, hydrated fetches (resolve canonical first, fallback to legacy), batched lookups, payload builders. Switch `redefine`, `repair_puzzles`, `retitle`, backfill reads to helper; switch upload / attach / redefine / repair writes to shared payload builder. Add `generator/core/clue_logging.py`; route messages via common event formatting. Update Cloudflare worker puzzle-clue fetch: resolve via `canonical_definition_id` first.
**Verification:** `python3 -m py_compile ...` (list of files); `python3 -m pytest tests/test_clue_canon_store.py tests/test_clue_canon.py tests/test_ai_clues.py tests/test_redefine.py tests/test_repair_puzzles.py tests/test_retitle.py -q` (129 passed).
**Outcome:** success
**Insight:** centralization of compatibility surface enables standardized clue-event logging.
**Promoted:** yes

---

### [2026-04-01] Batch clue-canon referee work across words to avoid per-comparison model thrash

**Context:** clue-canon backfill (~12k clues) LM Studio model thrash; requirement: backfill batching only, no regressions, logging/extensibility.
**Happened:** Trace hot path: `run_backfill()` merged one-by-one; each comparison ran full `primary -> secondary` cycle. Add `DefinitionRefereeInput` + `run_definition_referee_batch()` in `generator/core/ai_clues.py`; group requests under one primary + one secondary activation. Add `ClueCanonService._run_referee_batch()`. Replace backfill merge loop with `_MergeState` batch state machine in `generator/clue_canon.py`; preserve word order, collect up to `--referee-batch-size`. Keep single-item `resolve_definition()` behavior; preserve audit payloads; add batch log + summary.
**Verification:** `python3 -m py_compile ...` (list of files); `python3 -m pytest tests/test_ai_clues.py tests/test_clue_canon.py tests/test_redefine.py tests/test_repair_puzzles.py -q` (100 passed).
**Outcome:** success
**Insight:** high-volume referee jobs need dedicated batched execution; reuse vote semantics, move model activation to batch boundaries.
**Promoted:** yes

---

### [2026-04-01] Fix empty-arg crash in `run_clue_canon_backfill.sh`

**Context:** `./run_clue_canon_backfill.sh` crash: `line 34: args[@]: unbound variable`.
**Happened:** Trace to `set -euo pipefail` under macOS `bash 3.2.57`; expanded `"${args[@]}"` when empty. Rework wrapper: scan `"$@"` for `--apply|--dry-run`, branch on `$#`, build/expand `args` only after guarantee of element.
**Verification:** reproduce fix in `bash -lc` + `set -euo pipefail` (no args → `--apply`; args → passthrough).
**Outcome:** success
**Insight:** empty array expansion unsafe under `nounset` in macOS bash; prefer `$#`/`"$@"` gating.
**Promoted:** yes

---

### [2026-03-30] Persist rewrite structural rejection reasons, auto-balance overnight sizes, and centralize Supabase update logs

**Context:** fail rewrite attempts remember rejection causes; `run_batch_loop.sh` stop blind looping; centralized Supabase update logs.
**Happened:** Add `RewriteAttemptResult` in `generator/core/ai_clues.py`; keep `rewrite_definition()` compatible (return string default, expose `return_diagnostics=True`). Extend `ClueAssessment` with `rewrite_rejection_reason`. Update `generator/core/rewrite_engine.py` to persist last structural rejection when no candidate produced. Update `_synthesize_failure_reason()`: prefer verify/rating signals over structural. Add `generator/core/supabase_ops.py` with `execute_logged_update(...)`; switch current update sites. Extend `generator.loop_controller` with `--auto-size`, live grid counting, missing-size-as-zero balancing, smallest-size tie-break. Update `run_batch_loop.sh`.
**Verification:** `python3 -m pytest tests/test_loop_controller.py tests/test_ai_clues.py tests/test_rewrite_engine.py tests/test_batch_publish.py -q` (120 passed); `python3 -m py_compile ...` (list of files).
**Outcome:** success
**Insight:** persist rewrite structural failures in own channel; Python controller belongs against live Supabase inventory for overnight size balancing.
**Promoted:** yes

---

### [2026-03-26] Make both title models generate per round and share text cleanup with clue generation

**Context:** `gpt-oss-20b` empty title / weak behavior; align title generation with definition: lower temp, shared cleanup, parallel candidate generation.
**Happened:** Refactor `generator/phases/theme.py`: each round query both generators when `multi_model=True`; rate candidates cross-model; keep best valid across both. Lower temp `0.9 -> 0.3`. Extract plain-text cleanup from `generator/core/ai_clues.py` to `generator/core/llm_text.py`; reuse for titles (strip labels/markdown). Update theme tests.
**Verification:** `python3 -m pytest tests/test_text_rules.py tests/test_theme.py tests/test_retitle.py tests/test_repair_puzzles.py tests/test_batch_publish.py tests/test_ai_clues.py -q` (148 passed).
**Outcome:** success
**Insight:** share output-cleaning contract between title + definition generation; parallel generation reduces empty/noise failure modes.
**Promoted:** no

---

### [2026-03-26] Raise title-generation token budget to avoid gpt-oss reasoning-only empty outputs

**Context:** `run_title_improve.sh` repeated empty titles. `gpt-oss-20b` filled reasoning but left `message.content` empty at low token budget.
**Happened:** Reproduce chat call; confirm empty `content` at `max_tokens=50`. Increase title `max_tokens` in `generator/phases/theme.py` to `500`. Sync title prompts (`generator/prompts/system/theme.md`, `generator/prompts/user/title_generate.md`) word count `2-4 -> 2-5` to match validator.
**Verification:** `python3 -m pytest tests/test_theme.py tests/test_retitle.py tests/test_batch_publish.py tests/test_repair_puzzles.py -q` (88 passed).
**Outcome:** success
**Insight:** reasoning models exhaust budget on hidden content; generation paths need larger budget than answer length suggests.
**Promoted:** no

---

### [2026-03-26] Force deterministic `Fara titlu` when title generation never beats score 0

**Context:** all-failure title case stop producing random fallback labels; deterministic result if max score stays `0`.
**Happened:** Update `generator/phases/theme.py`: return `Fara titlu` with `score=0` + `used_fallback=True` instead of random label. Covers invalid rounds + low creativity scores. Add tests; verify batch/retitle/repair.
**Verification:** `python3 -m pytest tests/test_theme.py tests/test_retitle.py tests/test_repair_puzzles.py tests/test_batch_publish.py -q` (88 passed).
**Outcome:** success
**Insight:** deterministic failure labels beat random fallback titles for no-signal cases.
**Promoted:** no

---

### [2026-03-25] Add DEX-driven usage-label suffixes to clue prompts and clue text

**Context:** clues for rare/specialized senses carry usage suffix (e.g. `(arh.)`); source from `dex_definitions`; bias rating (justify rare, hurt common).
**Happened:** Extend `generator/core/ai_clues.py`: DEX-text label extraction, suffix precedence, normalization, prompt-context builders. `generate_definition()` / `rewrite_definition()` normalize to one suffix, remove gratuitous labels. Validation ignore label. Update prompt templates: verify treat suffix as guidance; rate score asymmetrically. Expand `tests/test_ai_clues.py`; update `tests/test_verify.py`. Harden verify tests: mock `LmRuntime.activate_primary()`.
**Verification:** `python3 -m pytest tests/test_ai_clues.py tests/test_verify.py tests/test_rewrite_engine.py -q`; `python3 -m pytest tests/test_redefine.py -q`.
**Outcome:** success
**Insight:** strip parenthetical suffixes before validation word-count/ending checks to avoid masking bad glosses.
**Promoted:** no

---

### [2026-03-25] Refresh redefine metadata/clue state after each persisted clue update

**Context:** `redefine` refresh puzzle-level Supabase metadata, persist `verify_note` + `verified`, keep title, backfill missing metadata.
**Happened:** Refactor `generator/redefine.py` to two-state flow: baseline puzzle re-eval -> candidate rewrite loop. `fetch_clues()` load `clue_number`, `verify_note`, `verified`. `build_working_puzzle()` import verify state. Persist via stable coordinate keys. After each write: advance in-memory puzzle, recompute assessment via `puzzle_metrics`, refresh `crossword_puzzles` (`description`, scores, `pass_rate`, timestamps). Add no-op/backfill handling. Expand tests.
**Verification:** `python3 -m pytest tests/test_redefine.py`; `python3 -m pytest tests/test_repair_puzzles.py`.
**Outcome:** success
**Insight:** key DB clue persistence by coordinates `(direction,start_row,start_col)`, not answer; duplicates legal.
**Promoted:** no

---

### [2026-03-24] Implement normalized-only Rust engine and pinned Python variant hydration

**Context:** Rust phase-1 grid fill own normalized only; dedupe input; forbid duplicates; rarity-aware normalized quality. Move variant resolution to Python; fix choice after selection.
**Happened:** Refactor `crossword_engine`: `words.rs` group by normalized key, dedupe, aggregate min rarity. `quality.rs` rarity-aware definability. `engine.rs` return `EngineError` (no panic), remove reuse, black-dot ladder search, single solution output, dict stats. `solver.rs` thread cancellation, remove `expect`. Python `generator/batch_publish.py`: group metadata by normalized word, random choice of variant once per clue after hydration, rewrite originals from pinned choice, inject pinned `word_type`/`word_original` into state. Update tests.
**Verification:** `cargo test ...`; `python3 -m pytest tests/test_batch_publish.py -q`; smoke run `cargo run ... --bin crossword_phase1 ...`.
**Outcome:** success
**Insight:** pin concrete variant immediately after normalized-only fill to prevent metadata randomization leak.
**Promoted:** yes

---

### [2026-03-23] Repair regressions after Python phase-1 removal

**Context:** CI failed in `test_ai_clues` / `test_verify` after phase-1 deletion; stale native processes.
**Happened:** Restore `ENGLISH_HOMOGRAPH_HINTS` in `generator/core/quality.py` for definition system prompt anti-English warnings. Update `generator/phases/verify.py` to pass `model=` only when name exists (fix mock contract). Stop background `crossword_phase1` processes.
**Verification:** `python3 -m pytest tests/test_ai_clues.py tests/test_verify.py -q` (49 passed); `python3 -m pytest -q` (369 passed).
**Outcome:** success
**Insight:** separate shared lexical helpers before ripping out implementation; verify all consumers.
**Promoted:** no

---

### [2026-03-23] Remove legacy Python phase-1 generator path after Rust migration

**Context:** delete old Python phase-1 code; no dead fallbacks / standalone commands.
**Happened:** Remove Python phase-1 from `generator/batch_publish.py`; make `_best_candidate(...)` Rust-only. Delete `generator/core/{constraint_solver,grid_template,word_index}.py`, `generator/phases/{generate_grid,fill}.py`. Simplify `generator/core/size_tuning.py` to size lists + retry floors. Remove `generate-grid`/`fill` from `generator/rebus.py`. Update `scripts/benchmark_phase1.py` (Rust only). Trim tests: delete `tests/test_{constraint_solver,grid_template,quality}.py`. Restore minimal `ENGLISH_HOMOGRAPH_HINTS` in `generator/core/quality.py`.
**Verification:** `python3 -m py_compile ...` (list of files); `python3 -m pytest tests/test_batch_publish.py tests/test_loop_controller.py -q` (47 passed); `cargo test ...`.
**Outcome:** success
**Insight:** none
**Promoted:** no

---

### [2026-03-20] Archive results3 and redesign next 100-experiment campaign

**Context:** forensic analysis of 99 experiments; new 100-run plan (removals first, file alternation).
**Happened:** Analyze `results.tsv` + `logs/results_exp150.json`. Write `build/experiment_reports/results3_campaign_review.md`. Archive to `generator/assessment/results3.tsv`. Recreate empty `results.tsv`. Redesign `scripts/run_experiments.py`: removal experiments first, strong file alternation. Tighten git commits (avoid `logs/`). Add runner tests.
**Outcome:** success
**Insight:** prompt backups must be authoritative; don't assume "keep" row matches current prompt tree in live-git campaigns.
**Promoted:** yes

---

### [2026-03-18] Add grammatical-form checks and richer experiment metadata

**Context:** experiments include prompt-pruning, grammatical-form checking, richer logs.
**Happened:** Update verify pipeline: pass category to `verify`. Add form-agreement instructions to `verify/rate/rewrite/definition`. Rewrite experiments: include removal attempts + form checks. Change descriptions: summary + file modified. Backfill campaign JSON/TSV. Add tests: verify metadata/word-type propagation, runner description formatting. Stabilize `test_verify.py` (mock `DexProvider`).
**Outcome:** success
**Insight:** isolate DEX prefetch in verify/rate unit tests for reliability.
**Promoted:** yes

---

### [2026-03-18] Recover partial 41-experiment campaign after power loss

**Context:** power loss during 100-experiment run; reconstruct edits + results.
**Happened:** Reconstruct `exp001`-`exp041` diffs from `scripts/run_experiments.py` + `logs/march17_campaign.json`. Generate reports in `build/experiment_reports/`. Backfill discards to `multistep_results.tsv`. Split monolithic log. Patch runner: per-experiment logs, persist discards in TSV, store `file/find/replace` in JSON. Identify `exp042` prompt leakage (no result).
**Outcome:** success
**Insight:** abnormal termination leaves prompt files ahead of recorded state; always diff against backup.
**Promoted:** yes

---

### [2026-03-20] Stop interrupted results run, archive results4, restore best prompt state, add top-k verifier semantics

**Context:** stop `results_exp100`; archive assessment; restore best backup; implement top-k verification (2-3 candidates).
**Happened:** Stop session. Copy `results.tsv` to `results4.tsv`. Restore `generator/prompts/` from `results_exp100_best` backup. Implement top-k verification: add `VERIFY_CANDIDATE_COUNT`, update verify prompts, add response parsing (numbered/comma lists), store in `ClueAssessment`, render to notes. Propagate "any candidate matches" semantics across pipeline + metrics. Add tests: prompt formatting, multi-candidate parsing, non-first success, note roundtrips, difficulty aggregation.
**Outcome:** success
**Insight:** adopt pass criterion across notes, metrics, batch publication, benchmark scoring for top-k utility.
**Promoted:** yes

---

### [2026-03-18] Rebuild multistep benchmark from March 17 and harden runner repeatability

**Context:** replace assessment words with March-17 candidates; multistep only; repeatable baseline.
**Happened:** Aggregate `metrics.json` to low/high TSVs (avg rebus scores). Rewrite dataset builder: 70-word set (low/med/high) + short-word caps + reused DEX. Run real baseline (67.8 composite, 28.6% pass). Patch `run_experiments.py`: stream logs, restore on interrupt, roll back discards in TSV, support campaign-specific paths/prefixes. Archive history; reset baseline to March-17 set.
**Outcome:** success
**Insight:** restore prompt + result state on discard to prevent poisoning hill-climbing.
**Promoted:** yes

---

### [2026-03-14] Add prefix stripping and forbidden stems to family check

**Context:** Romanian morphology (e.g. TIBETAN vs Tibet, NEINCEPUT) bypass suffix-only family check.
**Happened:** Add prefix stripping to `clue_family.py`. Add `forbidden_definition_stems()` + `_family_exclusion_note()` in prompts. Remove OU/URINARE presets.
**Outcome:** success
**Insight:** prefix stripping + forbidden stems essential for Romanian family checks.
**Promoted:** yes

---

### [2026-03-21] Make rewrite/failure flows use all verifier candidates, not only the first guess

**Context:** ensure top-3 verification respected in generation + evaluation.
**Happened:** Audit call chain; verify `verify_candidates` used in pass/fail, selection, metrics, notes. Fix rewrite/failure gap: `wrong_guess` only used first failed candidate. Patch `generator/core/ai_clues.py`: rewrite prompts mention full verifier output; failure history carries candidate lists. Patch `generator/core/score_helpers.py`: `_synthesize_failure_reason()` prefer candidate list. Update `batch_publish` / `redefine`. Add tests.
**Outcome:** success
**Insight:** use `verify_candidates` as primary signal; `wrong_guess` lossy compatibility field.
**Promoted:** yes

---

### [2026-03-21] Align assessment dataset DEX context with live expanded provider context

**Context:** ensure DEX expansion reaches all prompts (generation + assessment).
**Happened:** Trace call sites. Generation/rewrite/rating paths use `dex.get(...)` (correct). Verify intentionally lacks DEX (no hint leak). Fix gap: `prepare_dataset.py` reused old `dataset.json` strings (stale). Patch `_reuse_or_fetch_dex()`: prefer current provider `lookup()` from cache/Supabase. Add regression test.
**Outcome:** success
**Insight:** improvement in live context generator needs refresh path into cached assessment artifacts.
**Promoted:** yes

---

### [2026-03-21] Expand DEX semantic-base extraction to short first-definition patterns

**Context:** semantic context expansion for short definitions beyond redirect formulas.
**Happened:** Add expansion patterns in `generator/core/dex_cache.py`: synonym glosses, action/fact of X, property of X, unit fractions (`A <ordinal> parte dintr-un X`). Tighten target cleanup (drop punctuation/markers). Trigger only on first parsed definition to avoid false positives from citations. Add tests.
**Verification:** 540 entries reviewed.
**Outcome:** success
**Insight:** trigger expansion when first parsed definition structurally short + points to base lexeme.
**Promoted:** yes

---

### [2026-03-21] Add gitignored local DEX cache layer before Supabase

**Context:** stop Supabase extraction every run; use local cache folder.
**Happened:** Extend `DexProvider` to 4 layers: memory -> local disk -> Supabase -> dexonline. Directory: `.cache/dex_definitions/` (JSON per word: status, html, original, timestamp). Wire to `get()`, `lookup()`, `prefetch()`, redirect lookups. Store `not_found` results locally. Add tests: hit priority, negative cache, prefetch, persistence.
**Outcome:** success
**Insight:** local disk must sit in front of Supabase for all lookups (inc. redirect dereference) to avoid chatter.
**Promoted:** yes

---

### [2026-03-20] Fix DEX redirect parsing and one-hop semantic expansion

**Context:** redirect-style definitions (e.g. "Diminutiv al lui X") semantically thin; parser bugs in `FIRISOR`.
**Happened:** Audit `generator/core/dex_cache.py` + tests. Fix `_DefinitionExtractor`: inline closing tags decrement depth correctly. Add meta-pattern detection for short single definitions; implement 1-hop dereference to base lexeme; inject `Sens bază pentru X` lines. Add `uncertain_short_definitions()` collection + logging. Add tests: inline markup, `FIRISOR -> fir`, uncertain results.
**Outcome:** success
**Insight:** parser robustness + bounded dereference required for redirect-style entries.
**Promoted:** yes

---

### [2026-03-20] Validate baseline, smoke artifacts, and close lock/publication gaps

**Context:** runtime validation on real artifacts; close objective-alignment bugs.
**Happened:** Confirm baseline `generator/assessment/results.tsv` (composite 65.0). Smoke batches under `build/smoke_batch_verify*`. Fix initial provenance, markdown emphasis leak, `verified=False` blocker escape (rarity override bug). Fix lock bug: `clue.locked` required `verified=True`. Tighten `_is_publishable()`: min 0.5 pass rate. Harden `rate_definition()` retries: strict second prompt for invalid JSON. Fix structural definitions (`... asupra unei`), English titles, meta-prefix leaks. Add one-word gloss + dangling ending validation. Instrument rewrite churn metrics. Exact solves `4/22 -> 11/22` in smoke sample.
**Outcome:** success
**Insight:** alignment must cover rewrite gating, locking, publication thresholds, + metrics.
**Promoted:** yes

---

### [2026-03-20] Fix generator correctness and objective-alignment bugs on main

**Context:** correctness, objective alignment, metrics, tests pass.
**Happened:** Fix `_best_candidate()` early return (now searches all). Fix LM Studio model unloading (key vs instance id). Fix `defs.md` export (remove score residue). Fix selection/rewrite weighting for exact verification. Add word-difficulty aggregation: `wrong_guess`, `failure_kind`, blockers, scores, rarity overrides, type. Add tests: switching, ranking, search, export, metrics.
**Outcome:** success

---

### [2026-03-28] Make prompt autoresearch inspection side-effect free and narrow manifest-anchor coverage

**Context:** `prompt_autoresearch.py --dry-run` wiped state on error; manifest test failed on stale `v1` edits.
**Happened:** Add side-effect-free inspection: `--status` + `--dry-run` read existing state directly; bootstrap temp dir only if missing. Regression test for dry-run paths. Update manifest-anchor coverage in `tests/test_run_experiments.py`: validate active `v2` + `v3` manifests against live files. Add manual `v3` runbook.
**Verification:** 60 passed.
**Outcome:** success
**Insight:** inspection commands need separate read-only codepath; never call durable-state repair implicitly.
**Promoted:** no

---

### [2026-03-28] Rotate assessment ledger into results6 and clear working results.tsv

**Context:** preserve `results.tsv` history; fresh baseline start.
**Happened:** Copy ledger to `generator/assessment/results6.tsv`. Reset `results.tsv` to header-only. Keep code references on `results.tsv`.
**Outcome:** success
**Insight:** rotation = archive old, empty canonical for next run.
**Promoted:** no

---

### [2026-03-28] Prepare rewrite-focused v4 batch and rotate pre-v4 ledger into results7

**Context:** `v3exp016` win; isolate framing signal; rotate ledger for official baseline.
**Happened:** Add `v4` namespace (rewrite-only: rule re-additions, headers, compactness). Manifest: 8 single-file edits to `rewrite.md` (isolate ban deletion vs header compression vs length bias). Update docs + run commands. Archive to `generator/assessment/results7.tsv`. Reset `results.tsv`.
**Outcome:** success
**Insight:** isolate winning signal in next batch; probe deleted constraints vs reintroductions.
**Promoted:** no

---

### [2026-03-22] — Build durable prompt-autoresearch supervisor and reclassify active pilot ledger

**Context:** recoverable overnight prompt-improvement loop; stricter reclassification of pilot ledger.
**Happened:** Extend `benchmark_policy.py`: near-miss, research-signal, family-stopping constants. Refactor `run_experiments.py`: families/priority/targets metadata, gain/loss summaries from JSON, structured `keep / uncertain / discard`. New supervisor `scripts/prompt_autoresearch.py`. Initialize `build/prompt_research/`; store snapshots; replay logs; reclassify against baseline JSON; rewrite `results.tsv`. Reclassified pilot: `exp001`, `exp002` keep; rest discard. Add tests: classifier, family staleness, recovery, replay.
**Outcome:** success
**Insight:** overnight research must be resumable state machine over snapshots + JSON, not long session or flat manifest.
**Promoted:** yes

---

### [2026-03-21] Add shared runtime logging, shared rewrite engine, and structured assessment artifacts

**Context:** end-to-end operational refactor: logging, audit for short DEX, shared rewrite, aligned assessment, safer workflow.
**Happened:** Add `generator/core/runtime_logging.py`: human + UTC timestamps, path-safe run stamps, JSONL audit events. Wire to entrypoints; remove inline formatting. Emit `dex_short_definition_detected` events. Add `generator/core/model_session.py`; session-based model orchestration. Refactor `redefine.py` / `batch_publish.py` to `rewrite_engine.py`. Extend `run_assessment.py` for machine-readable JSON artifacts. Update `run_experiments.py`: consume JSON, classify decisions, persist summaries + backups.
**Outcome:** success
**Insight:** centralize runtime concerns (logging, audit, orchestration) before touching benchmark policy.
**Promoted:** yes

---

### [2026-03-21] — Curated 20260321 benchmark reset and new 100-experiment manifest

**Context:** replace assessment set with 70 curated words; 100-run campaign inc. multi-file bundles.
**Happened:** Patch `prepare_dataset.py`: curated tier map (30 low / 25 med / 15 high); refresh DEX. Regenerate `dataset.json`. Rework `scripts/run_experiments.py`: multi-file support, atomic edits, manifest validation. ordered campaign: 12 cleanup, 24 verify refreshes, 12 rewrite anti-distractor, 12 definition examples, 12 rate calibration, multi-file bundles. Regression test for anchor existence in live files.
**Outcome:** success
**Insight:** campaign manifests need anchor-existence tests to prevent silent skips from drift.
**Promoted:** yes

---

### [2026-03-22] — Analyze latest prompt autoresearch block after exp053

**Context:** analysis of autoresearch trials post-`exp053`; consistent gain/loss patterns; justify next families.
**Happened:** Audit `build/prompt_research/` artifacts (`exp054`-`exp067`). 8 consecutive discards. stale families: `definition_positive_examples`, `guidance`, `rate_rules` killed by collateral losses. `EPIGASTRU` universal gainer. `ETAN`, `OSTRACA`, `SAN` universal regessors. Next: `rewrite_structural_guidance`. Bundles unjustified (no signal in prerequisites).
**Outcome:** success
**Insight:** analysis only.
**Promoted:** no

---

### [2026-03-21] Implement pilot-first benchmark workflow around baseline_results_20260321

**Context:** March 21 curated benchmark target; pilot-led runner; block priorities; handle unstable controls (`ADAPOST`, `ETAN`).
**Happened:** load incumbent via `load_latest_kept_result()` from `results.tsv`. Add presets: `pilot`, cleanup, verify-examples, rewrite-anti-distractor, def-examples, rate-calibration, bundles. Add `--end-at`, `--summarize-log`. Classification: `verify-led`, `rewrite-led`, `rate-led`, `noisy`. Priority-order recommendations. Control-word watch logic. Created step commits.
**Outcome:** success
**Insight:** policy code store rules/ranges; incumbent scores belong in ledger. Stability policy needs assessment JSON.
**Promoted:** yes

---

### [2026-03-23] — Move `best_assessment.json` runner cache out of tracked prompt source

**Context:** `generator/prompts/best_assessment.json` cleanup; move to untracked folder.
**Happened:** confirmed usage in `run_experiments.py` only. Implement `best_result_summary_path()`: target `build/prompt_experiment_state/`. Keep read-only fallback for resume. Add tests.
**Outcome:** success
**Insight:** artifacts never beside source; prevents accidental commits / confusion with source-of-truth.
**Promoted:** yes

---

### [2026-03-23] — Expand v2 prompt autoresearch pool to ~40 narrow trials and fix rebuild-only side effects

**Context:** 40 v2 experiments (narrow hypothesis); fix `--rebuild-state` side effect (unintended run).
**Happened:** Reshape v2 families: `short_word_exactness`, `near_neighbor_exclusion`, `blank_output_concretization`, `rare_technical_noun_rescue`. Expand to 40 trials; single-file edits to `rewrite.md` / `definition.md`. Update thresholds in `benchmark_policy.py`. Fix `main()` in `prompt_autoresearch.py` to return after rebuild. Lazy `audit()` dir creation in `runtime_logging.py`.
**Verification:** 38 passed.
**Outcome:** success
**Insight:** maintenance commands must be side-effect free to prevent drift / noise.
**Promoted:** yes

---

### [2026-03-23] — Add v3 prompt+system lane, explicit model plumbing, and incumbent-snapshot integrity checks

**Context:** small mixed batch (temp + prompt); cleanup integrity; explicit model ids for assessment/rewrite (no default routing).
**Happened:** fix `scripts/run_experiments.py` facade gap for system lane. Fix constructor indentation. Fix autoresearch rebuild bug (restore live tree from rebuilt incumbent). Add explicit `model` passthrough to generate/rewrite/verify/rate. Add `--generate-temperature` / `--rewrite-temperature`. V3 manifest: 4 temp trials, verify minimization, rewrite generic exclusion, dedup/shortening. Rebuilt v3 state.
**Verification:** 82 passed.
**Outcome:** success
**Insight:** rebuilds must restore live tree + rewrite snapshot paths to pass validation.
**Promoted:** yes

---

### [2026-03-22] — Fix mobile rebus scroll-jump and pen-mode clarity

**Context:** mobile grid tap jump source; pencil mode implicit/unclear.
**Happened:** Audit `frontend/src/`. culprit: `scrollIntoView()` on active clue. Fix: auto-scroll only if container scrollable. Cell focus: `preventScroll: true`. UI: explicit `Creion` + `Pornit/Oprit` state, `aria-pressed`, distinct colors. Add `pencil-help.ts` modal; persist view in `localStorage`. Style modal; update CSS.
**Outcome:** success
**Insight:** constrain auto-scroll to internal clue pane in stacked layouts to prevent jumping.
**Promoted:** yes

---

### [2026-03-23] — Extend overnight loop to include 15x15 and retune 15x15 search budget toward ~1 minute

**Context:** `run_batch_loop.sh` include 15x15; ~1m phase-1 budget.
**Happened:** update `size_tuning.py` defaults; reduce `min_preparation_attempts` to `1` (avoid outer retries). Rust tune: `attempt_budget=50`, `max_nodes=5M`, `solved_candidates=3`. 15x15 smoke run: success in 65.5s.
**Verification:** 57 passed.
**Outcome:** success
**Insight:** widen inner search budget but shrink legacy whole-pipeline floors for nightly proportional loops.
**Promoted:** no

---

### [2026-03-23] — Replace batch phase-1 grid generation with Rust binary and remove rarity from search

**Context:** move grid creation from Python to Rust for speed; keep shell entrypoint; remove rarity dependency.
**Happened:** Add Rust crate `crossword_engine/`. Implement template generation, positional index, DFS solver (MRV/forward checking). JSON stdout contract. Update `run_batch_loop.sh`: build release binary first. Refactor `batch_publish.py`: shell out to Rust, reconstruct `Candidate` from JSON, difficulty without rarity. Keep Python search as benchmark fallback. Add tests + `benchmark_phase1.py`. 7x7 speedup: 59x.
**Verification:** 48 passed; cargo tests OK.
**Outcome:** success
**Insight:** preserve host-language candidate/markdown contracts for clean native migration; build up-front to fail fast.
**Promoted:** yes

---

### [2026-03-22] — Harden prompt-manifest anchor checks against already-landed replacements

**Context:** CI failure: `exp001` anchor mismatch (replacement already present).
**Happened:** update `apply_experiment()`: treat existing replacement as clean skip even if anchor absent. Update anchor test: accept current anchor or replacement text. Add unit test.
**Verification:** 353 passed.
**Outcome:** success
**Insight:** check semantic applicability, not literal historical text; accept already-landed state.
**Promoted:** yes

---

### [2026-03-22] — Fix prompt autoresearch incumbent persistence and safe rebuild semantics

**Context:** durable state drift in autoresearch (`state.json` vs `incumbent.json`).
**Happened:** Audit `prompt_autoresearch.py`. Root cause: persistence path overwrite Correct incumbent with stale baseline. Refactor: explicit bootstrap/resume/rebuild flows. Single durable-write helper `persist_campaign_state()`. Validator-driven hybrid resume. Atomic rebuild in temp dir + swap. Add error handling + status + tests.
**Verification:** 34 passed.
**Outcome:** success
**Insight:** durable-state repair must be atomic; rebuild in temp directory before swap.
**Promoted:** yes

---

### [2026-03-24] — Replace hardcoded Rust size table with formula + dictionary-length pressure

**Context:** explainable search settings scaling; word density character count driver.
**Happened:** Formula-based scaling for black density, budgets, tolerance, candidate floor. Add dictionary-length pressure: check long-word buckets, nudge black budget / template attempts. Add tests. 15x15 release probe: fail black counts 44..52.
**Verification:** 15x15 probe partial.
**Outcome:** partial
**Insight:** dictionary length histograms outperform board size alone for search settings.
**Promoted:** yes

---

### [2026-03-26] — Persist redefine/retitle run logs and force oldest-first maintenance ordering

**Context:** logs for redefine/retitle under `generator/output/`; deterministic oldest-first row order.
**Happened:** Update `redefine.py` / `retitle.py`: timestamped artifact dir, `run.log`, `audit.jsonl`. sort puzzles by `created_at` ASC + `id`. Add tests.
**Verification:** 47 passed.
**Outcome:** success
**Insight:** maintenance jobs need explicit artifact paths + deterministic row ordering.
**Promoted:** no

---

### [2026-03-26] — Retitle only duplicate-name puzzles, prioritize worst duplicate clusters, enforce normalized title uniqueness

**Context:** target repeated titles; ignore case/diacritics; unique normalized title enforcement.
**Happened:** Add `normalize_title_key()`: trim, collapse whitespace, strip punctuation, Romanian diacritic collapse. Update `retitle.py`: select normalized duplicates; order by cluster size DESC, then oldest-first. Inject existing keys into generation for rejection; post-gen guard. Bump `updated_at`. Refresh in-memory keys after update.
**Verification:** 38 passed.
**Outcome:** success
**Insight:** uniqueness checks must be run-stateful to prevent collisions with titles minted in same batch.
**Promoted:** no

---

### [2026-03-26] — Preserve DEX usage-category headings in parsed definition text

**Context:** capture register info from dexonline headings (e.g. `Arhaisme și regionalisme`).
**Happened:** Extend HTML parser in `dex_cache.py`: extract `tree-def`, then append category-tagged entries from usage-relevant headings. Exclude non-usage (e.g. `Sinonime`). Add tests.
**Verification:** 117 passed.
**Outcome:** success
**Insight:** Register metadata outside definition spans must be explicitly ingested.
**Promoted:** no

---

### [2026-03-26] — Prioritize never-repaired puzzles in redefine maintenance runs

**Context:** `run_definition_improve.sh` chewed recently repaired work while null rows waited.
**Happened:** Change `redefine.py` sorting: `repaired_at IS NULL` first, then missing metadata, then age. recently repaired lose priority to never-repaired/null-score rows. Add regression test.
**Verification:** 28 passed.
**Outcome:** success
**Insight:** `repaired_at` must dominate `created_at` heuristic for maintenance queue priority.
**Promoted:** no

---

### [2026-03-22] — Regroup prompt autoresearch families so stale-stop does not kill unrelated hypothesis classes

**Context:** supervisor safe-stopped on coarse families; missed positive examples + rule variants.
**Happened:** split families in `run_experiments.py`: `rewrite_anti_distractor` -> `framing` vs `structural_guidance`; `definition_examples` -> `negative` vs `positive` vs `guidance`; `rate_exactness` -> `counterexamples` vs `rules`. Update priorities in `benchmark_policy.py`. Update prerequisites. Rebuilt state.
**Verification:** 34 passed.
**Outcome:** success
**Insight:** families must correspond to hypothesis classes for stale-family logic efficacy.
**Promoted:** yes

---

### [2026-03-26] — Validate old DB titles before rerating and backfill missing `title_score` on retitle skips

**Context:** garbage legacy titles (e.g. `"<|channel|>"`) scored high in rerate; missing scores not persisted on skip.
**Happened:** Update `retitle.py`: old titles must pass structural validation (gate before rerating). Invalid titlesassigned `old_score = 0` locally (no LLM call). Backfill missing valid scores only on keep (`new <= old`). update on win only. Add tests.
**Verification:** 101 passed.
**Outcome:** success
**Insight:** validate legacy content with same structural rules as new content before rerating to avoid legitimizing corrupt values.
**Promoted:** no

---

### [2026-03-26] — Reduce retitle model thrash with phase-batched title generation, keep batch publish path stable

**Context:** LM Studio load/unload cycles in `run_title_improve.sh`; requirement: batch work for reuse, don't perturb publish path.
**Happened:** remove reactivation before rating in `theme.py`. Add retitle-specific batch runner: load generator once for batch, switch to evaluator once for GTP rating + Euro generation, switch back for Euro rating. `batch_publish` unchanged. Add `--batch-size`. Add tests.
**Verification:** 99 passed.
**Outcome:** success
**Insight:** batching for local runtimes belongs at orchestration boundary owning many items.
**Promoted:** no

---

### [2026-03-26] — Make `run_title_improve.sh` / `retitle --all` process all puzzles, prioritize missing title scores

**Context:** `--all` limited to duplicates; requirement: full table, prioritized missing `title_score`.
**Happened:** update `retitle.py`: order by `(missing score, created_at ASC, id)`. Add `--duplicates-only` for legacy behavior. Update CLI help / mention counts.
**Verification:** 97 passed.
**Outcome:** success
**Insight:** command flags must match naming; avoid second-stage subset filters on broad flags.
**Promoted:** no

---

### [2026-03-26] — Repair title dual-generator orchestration, per-model retries, and prompt shaping

**Context:** orchestration falsified logs; empty GTP outputs; overlong EuroLLM titles.
**Happened:** Refactor `theme.py`: activate generator only before call, activate evaluator only after structural pass. Per-model rejected-history shaping; inject corrective hints (e.g. `maximum 5 cuvinte`). Empty output: one short retry (no history). Remove `"(gol)"` from semantic rejection. Tighten prompts: Romanian-only, 2-5 words, ban coordinated forms, examples. Add tests.
**Verification:** 118 passed.
**Outcome:** success
**Insight:** just-in-time multi-model activation prevents log falsification + wastes in local single-active-model runtimes.
**Promoted:** yes

---

### [2026-03-28] — Only count meaningful puzzle progress after at least one filled letter

**Context:** opening puzzle marks `in progress` (no letters filled).
**Happened:** Update `progress-storage.ts`: add `hasFilledCells()`. Align progress meaning: >=1 non-empty cell. `main.ts`: clear empty snapshots, filter `in_progress` by filled cells, clean up empty saved entries.
**Outcome:** success
**Insight:** resume/status semantics need content-based threshold; avoid navigation side effects.
**Promoted:** no

---

### [2026-03-28] — Collapse selector filters behind compact disclosure on mobile

**Context:** mobile filter area too tall.
**Happened:** rework `puzzle-selector.ts`: compact top row (`Filtre (n)` + sort dropdown). Expandable panel for status/size/reset. Update CSS.
**Outcome:** success
**Insight:** disclosure + summary pattern for dense mobile filters.
**Promoted:** no

---

### [2026-03-28] — Refresh frontend puzzle discovery, local progress view, and lightweight challenges

**Context:** scale past 300 puzzles: size-first browsing, status filters, list state preservation, local progress/profile, local challenges.
**Happened:** Rework selector shell; list derivation in `main.ts` (browse state: status, hide, size, sort). selector UI: chips, sort select, results summary, challenge highlights. `challenges.ts`: derive status from player/progress. Backward-compatible `checksUsed` in storage. play-view: preserve list state/scroll. progress view: points, solved, time, challenges, badges, history. refreshed tutorials.
**Outcome:** success

---

### [2026-03-23] — Add first-round hybrid de-anchoring to redefine/repair without new prompts

**Context:** reduce anchor bias from bad existing clues; no prompt edits.
**Happened:** extend `rewrite_engine.py`: `hybrid_deanchor` path. For bad clues (failed/rebus<=4), first round builds two candidates: `rewrite` + `generate`. Cross-model verify/rate both; keep best. add bookkeeping (one branch run per clue). Enable in `redefine` / `repair_puzzles`. Add tests.
**Outcome:** success
**Insight:** implement de-anchoring as control-flow around existing families; reuse downstream evaluator.
**Promoted:** no

---

### [2026-03-23] — Implement repair pipeline for published puzzles with score backfill and dual timestamps

**Context:** automate repair for published puzzles: prioritize missing scores, then low scores; only update if improved; expose `created_at` + `repaired_at`.
**Happened:** Add DB metadata fields + migration. `puzzle_metrics.py`: shared scoring. `prompt_runtime.py`: preloads. `upload.py` / `batch_publish.py`: persist `description` + numeric metrics (no theme score text). Implement `repair_puzzles.py`: queue ordering, baseline eval, repair gate (`min_rebus` improvement), accepted-state writes. Update worker/frontend: return/display both dates, sort by `repaired_at ?? created_at`. Fix `redefine` adapter: recognize `H`/`V` direction.
**Outcome:** success
**Insight:** treat compact persisted enums (`H`/`V`) as first-class in DB adapters.
**Promoted:** yes

---

### [2026-03-23] — Archive v1 prompt campaign, add fragile-word guardrails, bootstrap narrow v2 campaign

**Context:** freeze v1; save `results5.tsv`; incumbent `exp002` seed; fragile-word guardrails; 12-run v2 manifest.
**Happened:** `benchmark_policy.py`: primary/secondary fragile-word watchlists; tighter v2 family stops. `run_experiments.py`: namespace support, v2 manifest (`v2exp001..012`), discard on primary fragile loss. `prompt_autoresearch.py`: set-specific state + family graphs. Archive to `results5.tsv`. Reset `results.tsv` to header + `exp002`. Restore v1 incumbent prompts. Bootstrap v2 state.
**Verification:** 37 passed.
**Outcome:** success
**Insight:** move repeated loser clusters to live classifier + family-stop policy before next campaign.
**Promoted:** yes

---

### [2026-03-26] — Persist title scores and tighten title screening across retitle + initial publish flows

**Context:** persist `title_score`; accept >= 8/10; reject `ALL CAPS`, 6+ words, or word-leakage >= 3 chars.
**Happened:** Add `text_rules.py`. Refactor `theme.py`: distinguish review from fallback selection. Surface rejection reasons. word-leakage: check normalized overlap (`min_length=3`). Structure `TitleGenerationResult`. persist score in `retitle.py`, propagate to initial publish + repair. Update schema/docs. Add tests.
**Verification:** 90 passed.
**Outcome:** success
**Insight:** screening rules belong before scoring; fallback titles last-resort only.
**Promoted:** yes

---

### [2026-03-29] — Point working policy at `baseline_results_20260329_v4exp001`

**Happened:** update `WORKING_BASELINE_DESCRIPTION` in `benchmark_policy.py`. refresh test expectations.
**Verification:** 52 passed.
**Outcome:** success

---

### [2026-03-29] — Apply phase-specific reasoning profiles for GPT-OSS

**Context:**request-time reasoning effort; stronger effort on generation/rating.
**Happened:** central chat helper: `gpt-oss` uses `medium` for generate/rewrite/rate, `low` for verify/tiebreak/title. `eurollm` skip. normalize long-output cap to `2000` (gen + title). `reasoning_tokens` unset (unpredictable).
**Verification:** 109 passed.
**Outcome:** success
**Insight:** short deterministic verifier/tiebreak calls cheap; creative/analytic passes spend reasoning budget.
**Promoted:** no

---

### [2026-03-29] — Add `reasoning_effort=low` for GPT-OSS chat completions

**Happened:** `model_manager.py`: add per-model `reasoning_effort`. PRIMARY (`gpt-oss`) to `low`. add `chat_reasoning_options()`. Route calls via shared helper in `ai_clues.py`. Add tests.
**Verification:** 107 passed.
**Outcome:** success

---

### [2026-03-29] — Confirm `v4exp001`, prepare `v5`, rotate ledger for fresh incumbent baseline

**Happened:** Verify confirmation rows (avg composite 75.9). Keep `v4exp001` working prompt. Add `v5` namespace (8 rewrite probes: header-signal isolation, blends, precision lines). Archive to `results8.tsv`. Reset `results.tsv`.
**Verification:** 57 passed.
**Outcome:** success

---

### [2026-03-28] — Fix equivalent-definition selection bias and add tier-balanced pass metric

**Happened:** `selection_engine.py`: `choose_clue_version()` prefer stronger assessed version for identical normalized text. Add `tier_balanced_pass_rate` to `run_assessment.py` (mean of per-tier pass rates).
**Verification:** 49 passed.
**Outcome:** success

---

### [2026-03-29] — Reset benchmark regime, refresh assessment DEX, and open `v6`

**Context:** reset regime around replicated evidence; fresh DEX; verify/rate/definition batch.
**Happened:** `run_assessment.py`: refresh `dex_definitions` via `DexProvider` before fallback. `run_experiments.py`: replicated incumbent/candidate batches (`--comparison-runs 3`); emit machine-readable summaries; use `tier_balanced_pass_rate` for keep/discard. Add `v6` (8 experiments). update policy/autoresearch/docs.
**Verification:** 61 passed.
**Outcome:** success
**Insight:** replicated machine-readable comparisons + refreshed context required when benchmark semantics drift.
**Promoted:** yes

---

### [2026-03-28] — Reframe `v4` rewrite experiments away from negative banned-token phrasing

**Context:** negated wording (e.g. “nu folosești engleză”) can bias weak models toward forbidden token.
**Happened:** rewrite `v4` manifest: use positive Romanian-register / referent-first / lexical-distance phrasing instead of negative bans.
**Outcome:** success
**Insight:** prefer positive target-state phrasing for older local models to avoid anchor bias.
**Promoted:** no

---

### [2026-03-28] — Promote `v3exp016` baseline to working incumbent and prep `v4`

**Happened:** Verify win row (composite 72.7). update `WORKING_BASELINE_DESCRIPTION`. refresh tests.
**Verification:** 51 passed.
**Outcome:** success

---

### [2026-03-30] — Raise rewrite/rate completion budgets for LM Studio medium reasoning

**Happened:** Trace `redefine` mass failure to `reasoning_effort="medium"` budget consumption. Reproduce on `PROMPT`/`ABA`. Update `ai_clues.py`: `rewrite_definition()` / `rate_definition()` max tokens `4000`. Add truncation logging (`purpose`, `model`, tokens, finish reason).
**Verification:** 77 passed; valid objects returned in live check.
**Outcome:** success
**Insight:** reasoning tokens consume completion budget; phase-specific retuning mandatory after enabling reasoning.
**Promoted:** yes

---

### [2026-03-31] — Make crossword grid letters bold

**Happened:** `.cell__input` `font-weight: 700` in `grid.css`.
**Outcome:** success

---

### [2026-03-31] — Stabilize definition bar height and auto-shrink long clue text

**Context:** reserve 3 rows; shrink long clue text; prevent grid jump; remove counter.
**Happened:** Stable 3-line height in `gamification.css`. `definition-bar.ts`: font-fit loop steps downward until fit. Hide `progress-counter`. Add `resize` refresh hook in `main.ts`.
**Outcome:** success
**Insight:** stabilize container height before fitting typography to avoid layout jitter.
**Promoted:** no

---

### [2026-03-31] — Make crossword backspace retreat to the previous square

**Happened:** `input-handler.ts`: `Backspace` clear + move back (if cell empty, move back first + clear). Wire touch remote in `main.ts`. Update keyboard copy in `index.html`.
**Outcome:** success
**Insight:** reuse physical editing primitives for virtual keyboard consistency.
**Promoted:** no

---

### [2026-03-31] — Run periodic maintenance on agent memory/config files

**Context:** audit memory files + agents for stale references/overlap/hierarchy.
**Happened:** Audit root files + `.claude/agents/`. sub-agent table matches directory. identified missing `SETUP_AI_AGENT_CONFIG.md` reference. Add file with full protocol.
**Outcome:** success
**Insight:** prioritize structural integrity (broken references) in periodic maintenance.
**Promoted:** no

---

### [2026-03-31] — Add upload timestamps, canonical backfill wrapper, and mobile pencil emoji

**Happened:** `upload.py`: stamp `created_at` + `updated_at` with UTC ISO. Correct activate hint. Add `run_clue_canon_backfill.sh` root wrapper (`python -m generator.clue_canon backfill`). add `--apply` default. Add tests. Update `README.md`. add `✏️` icon to mobile toolbar via `index.html` + `responsive.css`.
**Verification:** tests OK; build OK.
**Outcome:** success
**Insight:** wrap canonical commands as-is for safe operator entrypoints during refactor.
**Promoted:** no

---

### [2026-03-31] — Add touch-only crossword remote with synced direction control

**Context:** phone/tablet play avoid OS keyboard; sync direction icon.
**Happened:** add `touch-remote` to `index.html` (QWERTY + direction + delete). `grid-renderer.ts`: add `touchRemoteEnabled`; grid inputs `readOnly` + `inputMode="none"`. change focus to shell. `input-handler.ts`: expose virtual actions. wire `main.ts`: refresh remote icon on `activeDirection` change.toolbar compressed.
**Outcome:** success
**Insight:** route virtual actions through shared state transitions; make native inputs inert at rendering layer.
**Promoted:** no

---

### [2026-04-02] — Cut over clue reads to SQL view and freeze canonical rows

**Context:** implemented canonical clue plan: dry structure; migrate from `definition`; ingest 13k legacy clues.
**Happened:** phase-1 migration (schema); phase-2 migration (finalize). Add `crossword_clue_effective` view (compatibility until finalize). `clue_canon_store.py`: read from view; remove legacy write branching. Extend canonical identity: `word_type` + `usage_label`. Filter malformed UUIDs to prevent PostgREST crash. `clue_canon.py`: immutable rows (promote = new row); restricted near-match competition. backfill: seed only from verified clues; emit quarantine JSONL. update tests.
**Verification:** tests OK.
**Outcome:** success
**Insight:** move fallback logic to SQL view early; avoid duplicated compatibility branches + fragile UUID fetches.
**Promoted:** yes

---

### [2026-03-30] — Show full solution when reopening a solved puzzle in the UI

**Context:** solved tab puzzles showed empty grid.
**Happened:** Audit `main.ts`: solved puzzles skip `loadProgress()`. Solution payload was stored but not displayed. Add solved-view path: hydrate `cells` from `solution`, mark revealed, clear pencil marks, set `isSolvedView` flag. add readonly styles.
**Outcome:** success
**Insight:** Solved-history reopen must explicitly hydrate visible cells.
**Promoted:** no

---

### [2026-04-04] — Salvage stale `clue_canon` resume state without dropping in-flight active words

**Context:** resume crash `KeyError: 'ZGRIBURI'`. eligible workset smaller than checkpoint queue; preserve active word state.
**Happened:** Update `clue_canon.py`: reconcile checkpoint against fresh workset. Drop stale pending; preserve valid active words. defenses skip unexpected no-bucket words. Add resume counters to `BackfillStats`. Add checkpoint version validation. Expand tests.
**Verification:** 61 passed.
**Outcome:** success
**Insight:** serialized active work authoritative; pending queues advisory (revalidate on resume).
**Promoted:** yes

---

### [2026-04-04] — Remap Gemma reasoning controls to LM Studio chat endpoint semantics

**Context:** Gemma `low/medium` rejected by endpoint (expected `none`/omitted).
**Happened:** `model_manager.py`: map Gemma purposes to endpoint-safe values. `none` for default/light; omit params for thinking-enabled. normalize legacy aliases (`off->none`, `on->medium`). Add validation. Refresh tests.
**Verification:** 91 passed; live check OK.
**Outcome:** success
**Insight:** Follow actual wire behavior of `/v1/chat/completions`, not model capability metadata labels.
**Promoted:** yes

---

### [2026-04-04] — Backfill perf: DB-filter eligible clue rows, batch store writes, throttle checkpoints

**Context:** speed up backfill without LM parallelism. Main pain: load full corpus; per-word queries; per-row updates.
**Happened:** `clue_canon_store.py`: DB-side filtering (`verified=true`, `canonical_definition_id is null`). bulk prefetch; batched clue attachment; batched alias insertion. `clue_canon.py`: source eligible rows only. exact-merge identical canonicals before LLM requests. throttle checkpoint rewrites to 10s cadence. Expand tests.
**Verification:** 57 passed.
**Outcome:** success
**Insight:** DB workset filtering + per-word batching required for tractable resumable backfills.
**Promoted:** yes

---

### [2026-03-31] — Add canonical clue library, 6-vote referee, and legacy-safe prevention hooks

**Context:** collapse duplicates into canonicals. referee for near-duplicates. prevention hooks for generation.
**Happened:** Add types `clue_canon_types.py`, helpers `clue_canon.py`, adapter `clue_canon_store.py` (auto-detect schema). `ai_clues.py`: `clue_compare` JSON prompt; 6-vote referee (3 GPT-OSS + 3 EuroLLM). prompt-time prevention: inject existing canonicals into prompts. Wire canonical resolution to persistence (keep materialized legacy definition). offline command `python -m generator.clue_canon backfill`. Disagreement reports.
**Verification:** 66 passed.
**Outcome:** partial-success
**Insight:** resolve canonical ids at prompt + DB boundaries to keep markdown types + worker reads unchanged.
**Promoted:** no

---

### [2026-04-05] — Fix resume deadlock from stale in-flight referee requests

**Context:** backfill frozen after resume. state: `waiting=true` with stale request ids. silent no-op loop.
**Happened:** Add stale-wait detection to resume normalization. convert restored `waiting=true` items to deferred standalone canonicals (`resume_stale_wait`). commit via quarantine. add summary counters. main-loop deadlock guard (repair or error).
**Verification:** 121 passed.
**Outcome:** success
**Insight:** persist `waiting=true` only with durable request reconstruction or explicit resume downgrade.
**Promoted:** no

---

### [2026-04-05] — Fix backfill referee throughput by batching around immediate local outcomes

**Context:** model switch churn on tiny batches (requests=1). throughput bottle-neck.
**Happened:** Add batched referee collector: apply immediate local outcomes (`exact_merge` / `keep_separate`) without short-circuiting loop. Wait for min batch (`4`) for switches. Extended adaptive reporting (requests metrics). summary counters for activations / switches.
**Verification:** 120 passed.
**Outcome:** success
**Insight:** local outcomes and remote launches must co-exist in same cycle to prevent queue collapse to tiny launches.
**Promoted:** no

---

### [2026-04-05] — Monotonic backfill reducer, explicit referee diagnostics, exact state migration

**Context:** force progress when both models contribute. cleanup architecture. preserve resume behavior.
**Happened:** refactor state around shared types. Rework `ai_clues.py`: compare/referee records model-role contribution + diagnostics. Rework `clue_canon.py` to monotonic reducer: terminal resolution per cluster (`merge`, `promote`, `keep_separate`, `error`). shortlisted candidate per decision. v2->v3 checkpoint migration (convert `compare_index` state). summary counters.
**Verification:** 118 passed.
**Outcome:** success
**Insight:** referee tests + runtime must operate at attempt/diagnostic layer to catch contribution failures.
**Promoted:** no

---

### [2026-04-05] — Add bulk puzzle-definition audit wrapper for UI clue integrity

**Context:** scan puzzles for missing definitions / slot mismatches; minimal traffic.
**Happened:** Add `puzzle_definition_audit.py` + `run_puzzle_definition_audit.sh`. bulk-fetch metadata + clues. derive expected slots from `grid_template`. flag missing rows, blanks, duplicates, orphans, count mismatches. Paginated reads. JSON/JSONL reports. tests.
**Verification:** 18 passed.
**Outcome:** success
**Insight:** compare against `grid_template` structural truth after bulk read; avoid per-puzzle fetch loops.
**Promoted:** yes

---

### [2026-04-05] — Reorder finalize cutover migration to replace dependent view before dropping legacy column

**Context:** `cannot drop column definition ... because view crossword_clue_effective depends on it`.
**Happened:** fix migration order. NULL guard + `SET NOT NULL`. `CREATE OR REPLACE VIEW crossword_clue_effective` (canonical-only form). `DROP COLUMN IF EXISTS definition`. add preflight snippets.
**Outcome:** success
**Insight:** replace dependent compatibility views before dropping legacy columns.
**Promoted:** yes

---

### [2026-04-04] — Centralize LM Studio model registry and swap primary to Gemma 4

**Context:** replace `gpt-oss-20b` with Gemma 4; central change surface for future swaps.
**Happened:** Refactor `model_manager.py` to central registry. add active-pair selector. derive `PRIMARY` / `SECONDARY` aliases. `ModelConfig`: per-purpose reasoning. Gemma task-tuned `on/off`; EuroLLM omit. Update docs. Refresh tests.
**Verification:** 223 passed.
**Outcome:** success
**Insight:** registry must own transport ids + reasoning capabilities to prevent leak into purpose-specific code.
**Promoted:** yes

---

### [2026-04-05] — Bound `clue_compare`, defer stagnant backfill words, and suppress boilerplate pair churn

**Context:** backfill stuck on giant unresolved words; Gemma "thinking" budget overrun on tiny tasks.
**Happened:** Gemma `clue_compare` reasoning to `none`. reduce budget `2000 -> 120`. add truncation/slow-call logging. `_likely_cluster_match()`: ignore word boilerplate tokens; require informative tokens / high similarity. Add resumable stagnation tracking + `--max-stagnant-comparisons` budget (defer to quarantine). Serialize deferred signals.
**Verification:** 116 passed.
**Outcome:** success
**Insight:** better budgets + defer logic matter more than queue size when only giant formulaic words remain.
**Promoted:** yes

---

### [2026-04-05] — Simplify clue-canon referee to 2 phases and harden canonical insert conflicts

**Context:** switch churn; adaptive referee too slow. crash on duplicate canonical inserts (`LA`).
**Happened:** Replace 6-phase adaptive referee with strict 2-phase batch (Gemma pass + Euro pass). compact JSON (no `reason`). update batch metrics. launch floor `10`. `clue_canon_store.py`: catch `23505`; invalidate cache; refetch; reuse/bump instead of crash.
**Verification:** tests OK.
**Outcome:** success
**Insight:** cross-model unanimity beats alternating phases for local inference throughput.
**Promoted:** yes

---

### [2026-04-05] — Expand clue-canon backfill from verified-only nulls to all null pointers

**Context:** 4181 null pointers (unverified rows excluded).
**Happened:** `clue_canon_store.py`: include all `canonical_definition_id IS NULL` rows. `clue_canon.py`: treat unverified conservatively (exact reuse or singleton; no referee). Add counters for unverified attached / reuses.
**Verification:** 131 passed.
**Outcome:** success
**Insight:** source filtering must reflect all pointerless rows for operational completeness.
**Promoted:** yes

---

### [2026-04-05] — Add continuous canonical-fanout simplifier with fail-closed invalid-answer guards

**Context:** overnight simplifier; amortize model swaps; prevent corruption from malformed outputs.
**Happened:** add `simplify-fanout` wiring + engine `clue_canon_simplify.py` + wrapper. `clue_canon_store.py`: active-canonical fetch, repointing, supersede updates. `ai_clues.py`: merge-rewrite/validation helpers + fail-closed residue checks. add coverage for pair generation, sampling, rejections.
**Verification:** 142 passed.
**Outcome:** success
**Insight:** simplifiers need explicit batch state + fail-closed fallbacks to prevent corruption.
**Promoted:** no

---

### [2026-04-05] — Fix batch publish crash after Gemma swap; lower Gemma reasoning; add overthinking warnings

**Context:** `run_batch_loop.sh` stall post-Gemma swap. late upload crash. overthinking warnings.
**Happened:** audit logs; find `AttributeError: word_type`. Fix `ClueEntry` adapter. Harden upload: `getattr` fallback. Lower Gemma reasoning to `low` (registry). central `_chat_completion_create()` logging: emit `[warn reasoning_budget]` if thinking dominates. update tests.
**Verification:** 203 passed.
**Outcome:** success
**Insight:** publish loops need compatibility defaults at upload boundary + reasoning budget warnings.
**Promoted:** yes

---

### [2026-04-05] — Add central `--debug` LM streaming logs across LLM entrypoints

**Context:** live thinking visibility in `run.log`.
**Happened:** add process-wide LLM debug toggle + parser helper. Rework `TimestampedWriter` for stateful fragment emission (flush immediately). `ai_clues.py`: `_chat_completion_create()` use streaming only in debug mode. tag `[LLM thinking]` / `[LLM output]`. propagate `--debug` through all entrypoints + wrappers. tests for fragment logging.
**Verification:** tests OK; confirmed streamed thinking in manual smoke.
**Outcome:** success
**Insight:** debug depends on fragment-safe timestamped teeing; standard newline buffering masks thinking progress.
**Promoted:** yes

---

### [2026-04-06] — Fix false `english markers` rejects; add severity tags; remove timezone from human logs

**Context:** false `English markers detected` (diacritic splits); clear log tags; drop timezone.
**Happened:** Switched English-marker detection to Unicode-safe tokenization in `ai_clues.py`. Extended `llm_text.py`: strip labels/translations; pick first valid line. human timestamps local + timezone-free in `runtime_logging.py`. auto-prefix `[INFO]`. `format_human_log_line(...)` for manual writes. tag debug/warning paths. tests.
**Verification:** 96 passed.
**Outcome:** success
**Insight:** language guards must run after diacritic normalization; central severity policy avoids string drift.
**Promoted:** yes

---

### [2026-04-06] — Centralize LM completion budgets per model across clue + title flows

**Context:** Gemma hitting `finish_reason=length` at 2000 tokens.
**Happened:** `model_manager.py`: add `ModelConfig.max_completion_tokens` + `chat_max_tokens()`. budgets: Gemma 4000, GPT-OSS 2000, EuroLLM 200. replace literals in `ai_clues.py` / `theme.py`. update tests.
**Verification:** 129 passed.
**Outcome:** success
**Insight:** reasoning-token consumption requires completion budgets to move from purpose-literals into model registry.
**Promoted:** no

---

### [2026-04-06] — Reformat human logs to `timestamp LEVEL message` with post-level indentation

**Context:** place severity after timestamp; remove brackets; keep post-level indentation.
**Happened:** update `runtime_logging.py`: emit `YYYY-MM-DDTHH:MM:SS LEVEL ...`. normalize `[WARN] -> WARN`. leading spaces move to after severity (`INFO   message`). tests.
**Verification:** 28 passed.
**Outcome:** success
**Insight:** normalize severity before rendering indentation to prevent drift in visual layout.
**Promoted:** no

---

### [2026-04-06] — Add central no-thinking retry after completion budget is consumed by reasoning

**Context:** rescue calls that burn budget on reasoning with no answer.
**Happened:** Refactor `_chat_completion_create(...)`: orchestrate retry. Trigger: truncated thinking-enabled request, empty content, `reasoning_tokens >= max_tokens - 10`. retry forces `reasoning_effort="none"` + `max_tokens=200`. add override path in `model_manager.py`. tests.
**Verification:** 115 passed.
**Outcome:** success
**Insight:** rescue logic belongs in shared completion helper; key off budget exhaustion, not model name.
**Promoted:** yes

---

### [2026-04-06] — Disable Gemma reasoning only for verify

**Context:** Gemma `definition_verify` dominating truncations.
**Happened:** set `reasoning_by_purpose["definition_verify"] = None` for Gemma. `chat_reasoning_options()` omits param. tests.
**Verification:** 32 passed.
**Outcome:** success
**Insight:** apply per-purpose controls to noisiest paths.
**Promoted:** no

---

### [2026-04-07] — Make hidden-reasoning retry key off response, not request params

**Context:** Gemma `definition_verify` overran budget without retry (params omitted).
**Happened:** Audit `llm_client.py`: fix `_should_retry_without_thinking(...)` bailout on missing `reasoning_effort`. Rework gate to use response payload: truncated + empty content + high reasoning count/text. recursive-guard for fallback budget. tests.
**Verification:** 149 passed.
**Outcome:** success
**Insight:** response payload is reliable truth for thinking behavior, not request shape.
**Promoted:** yes

---

### [2026-04-07] — Remove canonical migration/backfill runtime after final cutover

**Context:** permanently post-cutover; remove transitional Mach; enforce invariants.
**Happened:** remove CLI maquinaria in `clue_canon.py`. delete `clue_canon_state.py` / wrapper. simplify `clue_canon_store.py`: constructor fail-fast; drop `is_enabled` / fallbacks / backfill helpers. Add health audit helpers. `clue_canon.py`: stop writing alias history. `clue_canon_simplify.py`: default to best survivor. rework `audit` (bad pointers, duplicates, bucket size, effective rows). update tests.
**Verification:** 122 passed.
**Outcome:** success
**Insight:** transition code is drift vector; collapse runtime onto new invariant after cutover.
**Promoted:** yes

---

### [2026-04-09] — Repo reshape into apps/engines/packages + eval subsystem; compatibility repaired

**Context:** planned reorg for clarity.
**Happened:** roots into `apps/`, `engines/`, `packages/`, `tools/`, `docs/`, `db/`. package `rebus-generator/` (workflow/domain/platform/evaluation/prompts). move assessment data to `evaluation/*`. Rust split by capability. frontend/worker to `apps/`. update entrypoints. Repair shims for legacy `generator.*` imports / patches.
**Verification:** build/tests passed.
**Outcome:** success
**Insight:** compatibility layers fail first on patches, not imports; module identity matters for legacy tests.
**Promoted:** yes

---

### [2026-04-07] — Add blind Gemma+EuroLLM consensus scoring for verify/rate/title

**Context:** scoring decisions from blind pair; Gemma no-thinking retry as single vote.
**Happened:** low-level helpers single-model; orchestrator pair-aware. `llm_client.py`: tag responses `reasoning` / `no_thinking_retry`. `ai_clues.py`: consensus helpers (`consensus_score`, `combine_definition_ratings`). `verify.py`: per-model vote capture on `ClueAssessment`; run both models blind. `puzzle_metrics.py`: `scores_complete` requirement for metadata. `retitle.py`: title creativity pair consensus. `rewrite_engine.py`: remove single-evaluator overrides.
**Verification:** 284 passed.
**Outcome:** success
**Insight:** aggregate to pair consensus at orchestration layer; keep single-model prompts intact.
**Promoted:** yes

---

### [2026-04-07] — Add loaded-model-aware scheduler; remove duplicate outer retries on short tasks

**Context:** title generation paid Gemma attempt after retry; scheduler for residency optimization.
**Happened:** Add `model_aware_scheduler.py`: per-model queues. prefer loaded model until drain. Switch model only when empty. early terminal conclusions (no redundant votes). refactor verify/rate + title rating to scheduler. title batch scoring by model phase. `ai_clues.py`: add caps for short tasks; stop outer retries if `no_thinking_retry` completed. tests.
**Verification:** 240 passed.
**Outcome:** success
**Insight:** scheduler must respect existing activation seams for production efficacy + testability.
**Promoted:** yes

---

### [2026-04-07] — Make dispatch mandatory for all production generator pipelines

**Context:** extend scheduler across all LLM calls; mandatory dispatch layer.
**Happened:** add `llm_dispatch.py` (entrypoint over scheduler). switch existing users (verify, theme). move generation flows to dispatch: define missing clues, title generation, retitle batch. `rewrite_engine.py`: model choice via local config; LLM work via dispatch. patch `score_helpers.py` tie-break. remove legacy warm-up activations. tests.
**Verification:** 294 passed.
**Outcome:** success
**Insight:** dispatch layer removes runtime-switch drift; helper removes transport drift.
**Promoted:** yes

---

### [2026-04-07] — Add unified `run_all` supervisor with queue telemetry and shell wrapper aliases

**Context:** single daemon for all topics; respect one-loaded-model constraint; queue telemetry.
**Happened:** add `run_all.py` supervisor. singleton lock. topic caps. puzzle/word claims. queue admission freeze when both model sides have backlog (starvation guard). `lm_runtime.py`: `switch_callback` hook for state logging. `batch_publish` accept runtime. `clue_canon_simplify.py` support bounded batches. add wrapper `run_all.sh`; aliases for topic scripts. tests.
**Verification:** 194 passed.
**Outcome:** success
**Insight:** "never switch until empty" needs admission freeze to prevent opposite model starvation.
**Promoted:** yes

---

### [2026-04-07] — Document `run_all` as local-claim supervisor, not event bus

**Context:** clarify concurrency boundaries; protect via tests.
**Happened:** update `README.md`: `run_all` section (local claims, no event bus, single-process limit). module docstrings: word-claim protection, admission freeze. tests: cross-topic claim isolation, admission freeze blocking.
**Outcome:** success
**Insight:** freeze safety boundaries in docs to prevent event-bus inference.
**Promoted:** no

---

### [2026-04-07] — Convert `run_all` to one-active-job-per-topic slots with step scheduling

**Context:** concurrent progress across topics (redefine, generate, retitle, simplify) under LM constraint.
**Happened:** replace job queue with topic slots + resumable jobs. Add `StepState` / `JobState`. Drain all runnable steps for loaded model before switch. refill slots on completion. `redefine` split to stages: fetch, baseline-eval, rewrite, persist. share persistence logic in `redefine.py`. update `README.md` + tests.
**Verification:** 181 passed.
**Outcome:** success
**Insight:** multi-topic concurrency under one model needs resumable stages + topic slots.
**Promoted:** yes

---

### [2026-04-07] — Remove legacy unattended wrappers; make `run_all` supervisor-native

**Context:** `simplify` killed orchestrator via `SystemExit`. consolidate on `run_all`.
**Happened:** supervisor-safe simplify primitives in `clue_canon_simplify.py`. turn simplify to staged bucket work. `_run_step(...)` convert `SystemExit` to topic failure. rework `generate` to staged steps (`select_size`, `fill_grid`, `define`, `rewrite`, `title`, `publish`). share publish logic in `batch_publish.py`. delete old shell wrappers. Update docs/arch.
**Verification:** 200+ passed.
**Outcome:** success
**Insight:** supervisor paths need pure primitives; wrappers with process exit / resume ownership unsafe.
**Promoted:** yes

---

### [2026-04-07] — Tighten mobile clue bar and move touch keyboard above action buttons

**Context:** mobile space efficiency; grid -> keyboard -> buttons stack.
**Happened:** `definition-bar.ts`: stacked clue badge (`Vert./Oriz.` over number). retune font fitting. denser 2-line clue box. `gamification.css`: centered definition text. `index.html`: move `#touch-remote` above `.toolbar`.
**Verification:** build OK; screenshot confirmed layout.
**Outcome:** success
**Insight:** stack metadata vertically in badge column to reclaim width for clue text.
**Promoted:** no

---

### [2026-04-07] — Add worker lane to `run_all`; split baseline + retitle rounds into resumable phases

**Context:** Rust `fill_grid` monopolized thread; requirement: concurrent LM + local Prep.
**Happened:** add `execution_mode` (`background_non_llm`). single `ThreadPoolExecutor` worker lane. lane-aware logs + telemetry. `fill_grid` move to worker lane. `redefine` baseline split (verify, rate, finalize). `retitle` split (primary/secondary rounds, persist). tests.
**Verification:** 147 passed.
**Outcome:** success
**Insight:** recover overlap with separate worker lane for long local prep.
**Promoted:** yes

---

### [2026-04-07] — Extract `run_all` supervisor package; split redefine rewrite/persist and retitle persist for lane purity

**Context:** god-file removal; maintainability; starvation removal.
**Happened:** move orchestration to `generator/supervisor/`. topic pollers, scheduler, per-topic job modules. rewrite-session primitives in `rewrite_engine.py`. stages: `initial_verify`, `initial_rate`, `prepare_round`, `score_round`, `finalize_round`, `persist_prepare`, `persist_apply`. split persistence from LLM stages in `redefine.py` / `retitle.py`. update tests.
**Verification:** 155 passed.
**Outcome:** success
**Insight:** split mixed helpers into `prepare` (LLM) and `apply` (pure persistence) for lane purity.
**Promoted:** yes

---

### [2026-04-09] — Add run-local LLM policy, real preflight, stall detection, fairness, and stop artifacts to `run_all`

**Context:** unattended reliability; preflight smoke checks; throughput-stall stop; fairness for repeated items.
**Happened:** CLI knobs: gemma reasoning, stall window, `--llm-preflight`. run-local policy in `llm_client.py`: task-specific reasoning/caps during `run_all`. telemetry: per-purpose counters, truncation downgrade, slow-call detection. `_preflight(...)`: load models, tiny smoke request, fail fast on malformed response. `lm_runtime.py`: activation timing. `scheduler.py`: progress memory, fairness deprioritization, stall detection, `run_summary.json`. Update pollers. tests.
**Verification:** 148 passed.
**Outcome:** success
**Insight:** unattended-run caps/downgrades must be opt-in to avoid polluting standalone scripts.
**Promoted:** yes

---

### [2026-04-09] — Audit Cloudflare changes after repo reorg + compactions

**Context:** infra drift check post-reorg.
**Happened:** inspect `wrangler.toml`, code, workflows. Worker entry correct (`apps/worker`). Identified follow-ups: deploy roots, secrets/vars (`ALLOWED_ORIGINS`), Vite API-base, stale docs paths.
**Outcome:** success
**Insight:** infra drift lives in deploy roots + CORS, not runtime entry module.
**Promoted:** no

---

### [2026-04-09] — Complete repo migration; remove legacy Python namespace + source-artifact tree

**Context:** old code removal; app functionality preservation.
**Happened:** delete `generator/` tree + transitional packages. Move last stray modules: config to `platform/config.py`, loop controller to `cli/`, scheduler to `workflows/run_all`. Rewire imports to real owners (`platform`, `domain`, `workflows`). wordlist to `engines/crossword-engine/tests/fixtures/`. prompt-campaign to `build/evaluation/`. repo-root `rebus_generator/` bootstrap package. update CI/docs. install worker deps; verify type-check.
**Verification:** 577 passed; cargo tests OK; build OK.
**Outcome:** success
**Insight:** repoint imports before deleting wrappers to surface relative imports quickly.
**Promoted:** yes

---

### [2026-04-10] — Restore `generate.service` compat export for `run_all` metadata injection

**Context:** `AttributeError: module ... has no attribute '_inject_word_metadata'` in `run_all`.
**Happened:** Trace crash to refactor split. Restore `_inject_word_metadata` import in `generate/service.py`. restore `DexProvider`, `_restore_best_versions`, `generate_title_for_final_puzzle_result` (facade gaps). Add regressions.
**Verification:** 72 passed.
**Outcome:** success
**Insight:** facades must re-export every private helper called by live orchestrators.
**Promoted:** no

---

### [2026-04-10] — Keep `run_all` alive when one generate size is deterministically unsatisfiable

**Context:** 14x14 unsat stop run; cooldown existed but quarantine escalated to whole-run stop.
**Happened:** `workflows/run_all/state.py`: deterministic `fill_grid` unsat failures mark size failed/cooled but continue supervisor. `scheduler.py`: stop error handling if terminally failed. tests.
**Verification:** 73 passed.
**Outcome:** success
**Insight:** break code path = stop; unsatisfiable size = penalize/skip.
**Promoted:** yes

---

### [2026-04-10] — Sweep stale Python imports after repo refactor

**Context:** repo-wide search for pre-refactor import paths.
**Happened:** AST-based scan across all Python files. found stale imports in `build/experiment_reports/` (deleted `generator.config` / `dex_cache`). Update to `platform.config` / `platform.io.dex_cache`. verify with `rg`.
**Verification:** scan FOUND=0.
**Outcome:** success
**Insight:** AST resolution safer than raw grep for namespace-package realities.
**Promoted:** no

---

### [2026-04-10] — Repo-wide Python architecture pattern analysis

**Context:** pattern recognition, reuse candidates, module extraction opportunities.
**Happened:** inventory 196 files. concept-tagging pass. wrote `python_file_concepts.tsv`. Re-read orchestration cluster representatives, staged workflows, boundaries, persistence, test mirrors. Wrote review `python_architecture_review.md`: extraction backlog, smells, unification calls.
**Outcome:** success
**Insight:** structural duplication (loops, shells, facades) dominates algorithmic duplication. Extract engine/scaffold primitives first.
**Promoted:** yes

---

### [2026-04-10] — Unified runtime-core refactor: orchestration core + staged jobs + guard ownership + facade removal

**Context:** unify primitives, staged contracts, guard direction; delete facades.
**Happened:** Add shared scaffolding in `platform/orchestration/` (`WorkItem`, `WorkStage`, `RunLedger`) + `workflows/shared/staged_job.py`. Rebase `run_all` on owners. Move validation to `domain/guards/`. normalize `retitle`. Split workflows to `runtime.py`. Repoint scripts/tests. delete dead modules. fix constructor-order bug (ledger helper).
**Verification:** 157 passed.
**Outcome:** success
**Insight:** rebase orchestrator onto shared primitives before deleting facades.
**Promoted:** yes

---

### [2026-04-10] — Retune unattended `run_all` short-form LLM policy and bound redefine rewrite rounds

**Context:** startup crash fix; truncation / slow-progress investigation.
**Happened:** Seed supervisor load-seconds from runtime counters. tighten Gemma policy: `definition_verify`, `title_*`, `clue_compare`, `clue_tiebreaker` default to `reasoning="none"`. raise caps to `256-320`. trigger retry on truncated malformed JSON. repoint `referee` to capped budgets. Reduce rewrite rounds: cap to worst 12 candidates; reject locally invalid generated rewrites up-front. restore `time` import. tests.
**Verification:** 270+ passed.
**Outcome:** success
**Insight:** policy must model parser-invalid partial responses, not just empty completions.
**Promoted:** yes

---

### [2026-04-10] — Timed Rust phase-1 search + edge-single template policy + stable tie RNG + rewrite-guard owner fix

**Context:** phase-1 large sizes failing fast; edge-single black policy; equal-rank choice randomness; rewrite crash.
**Happened:** Rust phase-1: per-`black_step` wall-clock budgets (`5-15s`). keep inward search until deadline; outward pass after solvable candidate.Engine stats: inward/outward counters, rejection buckets. Relax template: interior singletons forbidden, edge singletons allowed if orthogonal 2+ coverage (reclaim space). Python: `stable_tie_rng(...)` for accidental equal-rank choice sites. Fix rewrite bug: call `definition_guards` directly. tests.
**Verification:** Rust tests OK; Python tests OK.
**Outcome:** success
**Insight:** random tie-break in unattended flows must use stable RNG derived from run identity for reproducibility.
**Promoted:** yes

---

### [2026-04-10] — Align generator-time singleton guard with final validator; benchmark now records failures

**Context:** grid generation aborted at 14x14; generator guard more restrictive than validator.
**Happened:** shared Rust placement-time helper in `template/validate.rs`: match final slot policy (interior singletons rejected, edge singletons allowed). update procedual + incremental generators. Update `benchmark_phase1.py`: continue after failures, emit full rows, save JSON report. tests.
**Verification:** 135 passed; live rerun successful.
**Outcome:** success
**Insight:** align generator guards with validator first to avoid misdiagnosing unsat.
**Promoted:** yes

---

### [2026-04-10] — Lowered initial black targets, split inward/outward budgets, beam outward optimization, multiline grid logs, progress-aware stall detection

**Context:** lower initial blacks; separate outward budget; log grid visual; progress-aware stall detection.
**Happened:** Reduce Rust `target_blacks`. Add `outward_time_budget_ms`. Replace outward removal with beam frontier rankers: fewer blacks, fewer edge singletons, structural quality, stable tie break. Report chosen candidate's `edge_singletons`. multiline stderr logs (`+` for blacks). stall detection key off `last_progress_at` (step completion, stage transition). tests.
**Verification:** 120+ passed; benchmark successful (reduced black counts).
**Outcome:** success
**Insight:** separate cumulative search counters from chosen-grid quality metrics.
**Promoted:** yes

---

### [2026-04-10] — Exact low-start retune, zero-black outward skip, 15s benchmark default

**Context:** implement per-size base target table; inward found zero-black solutions should skip outward.
**Happened:** Replace formula in `settings_for_size()` with exact base target table (`7->0...15->38`). inward/outward defaults `15000ms`. Rust: add `outward_skipped_zero_black`. skip outward pass if zero-black found. update stats/logs. tests.
**Verification:** 138 passed; benchmark OK.
**Outcome:** success
**Insight:** monitor base vs tuned starts; dictionary tuning adds black bonuses.
**Promoted:** yes

---

### [2026-04-11] — Replaced runtime dictionary black inflation with precomputed sidecar scarcity profile

**Context:** remove runtime dict tuning; black-count ownership in size table; precompute scarcity beside `words.json`.
**Happened:** Add Rust binary `crossword_dictionary_profile`. emits per-size counts, density, positional rarity (surprisal) to `words.profile.json`. Remove `tune_settings_for_dictionary(...)`. `plan_search_effort(...)` scales effort only. Solver: MRV slot selection breaks ties via anchored rarity; candidate ordering uses open-position rarity. Python helper `rebuild_dictionary_profile(...)`; automate in `run_all.sh` + benchmark. tests.
**Verification:** cargo tests OK; 121 passed.
**Outcome:** success
**Insight:** scarcity profile must be sidecar artifact from same filter path as phase-1 to avoid structural settings drift.
**Promoted:** yes

---

### [2026-04-11] — Reset-safe puzzle scoring and canonical ranking after DB quality resets

**Context:** generate quarantine on `pass_rate=0` after DB reset; preserve functionality after resets.
**Happened:** Reset-safe assessment: keep live `verified_count` / `pass_rate` even if pair evaluation incomplete. aggregate quality null when incomplete. Generate gate distinguish missing defs vs low pass rate vs incomplete pairs. shared canonical ranking helper: deterministic neutral ordering if evidence blank. Rewrite: `initial_passed` from fresh verification.
**Verification:** 152 passed.
**Outcome:** success
**Insight:** DB quality fields cache/history only; never treat as runtime truth for logic gates.
**Promoted:** yes

---

### [2026-04-11] — Fixed CI regressions in preflight artifact persistence, rewrite verify compatibility, and Dependabot security resolution

**Context:** CI failures: preflight writes, rewrite verify tuple; Dependabot `serialize-javascript`.
**Happened:** `run_all._preflight(...)`: persist `preflight.json` even on bootstrap failure. `rewrite_session_initial_verify(...)`: accept normal tuple, fallback to deriving from state for compat. `apps/frontend/package.json`: npm `overrides` for `serialize-javascript@^7.0.5`. lock refresh.
**Verification:** CI regressions pass; build OK.
**Outcome:** success
**Insight:** early bootstrap failures should emit artifacts for durable record. Transitive npm updates via overrides + lock refresh.
**Promoted:** no

---

### [2026-04-11] — Canonical conflict recovery + publish gate swap + run_all drain metrics

**Context:** publish crash on conflict; model churn; old publish gate.
**Happened:** Add exact-key canonical reload in `ClueCanonStore`: lookup on `23505` conflict; short retry/backoff. Publish gate swap: `verified_count >= 1` (replaces pass-rate threshold). supervisor: same-model drain cycles. summary fields: `activation_overhead_seconds` / `loaded_model_drain_switches`. nested-activation warnings. flatten job helpers (no inner switches).
**Verification:** 60+ passed.
**Outcome:** partial success
**Insight:** supervisor must own model switching; aggregate consensus at orchestration layer.
**Promoted:** no

---

### [2026-04-11] — Hybrid run_all work units, compat shims, drain-loop dedupe

**Context:** inner-loop breakdown using hybrid units; maintainability.
**Happened:** unit metadata on staged-job primitives (`phase`, `unit_id`). Supervisor: `plan_ready_units()` / `apply_unit_result()`. one global loaded-model drain. `RunAllRewriteSession`: atomic generate/redefine work (per-clue verify/rate, candidate gen/verify/rate). Compat shims for coarse helper tests. Drain-loop dedupe guard: prevent rerun of unchanged unit in cycle.
**Verification:** 173 passed.
**Outcome:** success
**Insight:** unit-planning supervisor needs drain-loop dedupe to avoid infinite retry within cycles.
**Promoted:** yes

---

### [2026-04-11] — Migrated Python dependency management and execution from pip/venv to uv

**Context:** clean environment setup; remove `PYTHONPATH` hacks.
**Happened:** root `pyproject.toml` (hatchling). dependencies + dev group. Configured wheel targets for `packages/`. remove legacy requirements / `sitecustomize.py`. update shell scripts: `uv run python`. update CI to `astral-sh/setup-uv@v5`. update docs.
**Verification:** 600 passed; sync OK.
**Outcome:** success
**Insight:** root `pyproject.toml` + `uv` provides robust multi-package source path management without site hacks.
**Promoted:** yes

---

### [2026-04-11] — Fix `run_all` redefine persist crash from mixed rewrite-session contracts

**Context:** `AttributeError` on `finish_rewrite_session`. `run_all` uses `RunAllRewriteSession`.
**Happened:** audit log; confirm contract mismatch. Rework redefine job: call `self.rewrite_session.finish()` directly. `RunAllRewriteSession`: add cached `final_result` + idempotent finish. tests.
**Verification:** 43 passed.
**Outcome:** success
**Insight:** session classes in unattended flows need stable finish contract.
**Promoted:** yes

---

### [2026-04-17] — run_all efficiency and reliability pass

**Context:** quality-preserving optimization: DEX churn, title-mode, logging, retries, rewrite waste.
**Happened:** hoist puzzle-scoped DEX providers to JobState (reuse per job). title rating respect `multi_model=False`. prompt-body logging behind `--debug`. preserve multi-line failure logs. periodic summary snapshots in heartbeat. retry/backoff for Supabase 500s. bounded rewrite fan-out. skip rating if primary verify fail answer match. quarantine repeated unchanged words. tests.
**Outcome:** in progress

---

### [2026-04-17] — DEX compound clue handling

**Context:** `[DEX] not found` for compound strings (e.g. `AURI - AMUS`).
**Happened:** Update `DexProvider.for_puzzle()`: expand compounds to atom prefetches before write. `lookup()` / `get()` resolve via components. tests.
**Verification:** 90 passed.
**Outcome:** success

---

### [2026-04-18] — Redefine fallback to scored canonicals + strict short-word leakage

**Context:** reuse fallback when rewrites exhaust (weighted by scores, penalized by usage); strict leakage for `OUA`, `OS`.
**Happened:** scored-only canonical filtering for prompts. seeded weighted fallback selector: `(score sum) / (usage + 1)`. `ClueCanonStore` lookup by id. `apply_scored_canonical_fallbacks()` in redefine: hydrate assessment from representative row / metadata. Tighten validation: remove short-word bypass; add local prefix/subform leak guard. tests.
**Verification:** 130+ passed.
**Outcome:** success
**Insight:** strict short-word leakage needs validator-local guard, not global family matcher relaxation.
**Promoted:** yes

---

### [2026-04-18] — Frontend completion overlay reset for puzzle switches

**Context:** stale `REZOLVAT` stamp on new puzzle.
**Happened:** Trace to `bootstrap.ts` (navigation never cleared UI). Extract `completion-overlay` helper. reset overlay before transitions / puzzle load. replay animation from clean state. `jsdom` regression test.
**Verification:** build OK; 21 passed.
**Outcome:** success
**Insight:** reset transient celebration UI on context switch if display not derived from state.
**Promoted:** no

---

### [2026-04-19] — Shared scored-canonical fallback for redefine + generate

**Context:** DRY shared fallback; extend to generate; stop doomed title attempts if publishability blocked.
**Happened:** extract logic to `canonicals/scored_fallbacks.py`: selection, rehydration, synthesized assessment, state application. shared clue helpers: placeholder/missing check, incomplete-pair check. redefine wrapper for compat. generate fallback: unresolved-only define-finalize flow. pre-title guard (skip on unresolved). tests.
**Verification:** 128 passed.
**Outcome:** success
**Promoted:** no

---

### [2026-04-20] — run_all placeholder recovery + same-text canonical hydration

**Context:** `run_all` quarantine on placeholder clues / incomplete evaluation.
**Happened:** fix `da239a0` strict gate (unresolved admit gap). Admission: include placeholder clues. tiered fallback selection (`exact_type_usage -> ... -> same_word`). hydrate same-text assessment only for unresolved generate clues. clearer logs. tests.
**Verification:** 100 passed.
**Outcome:** success
**Insight:** same-text canonical fallback = assessment repair; required for metadata-dependent quality gates.
**Promoted:** yes

---

### [2026-04-20] — Gemma reasoning transport realignment for LM Studio

**Context:** Gemma warning on `low` reasoning. endpoint expects `none` / omitted.
**Happened:** audit registry + endpoints. `ModelConfig` transport config. separate abstract intent from request params. Gemma: omitted for thinking; `none` for no-thinking. reasoning-capability cache. retries for invalid reasoning values. tests.
**Verification:** 169 passed; live check OK.
**Outcome:** success
**Insight:** omitted params can mean "thinking on"; key budget logic on intent, not presence.
**Promoted:** yes

---

### [2026-04-20] — puzzle definition audit canonical orphan coverage

**Context:** report unreferenced canonical definitions for SQL removal.
**Happened:** `ClueCanonStore.fetch_canonical_rows()`. extend audit: gather clue-referenced ids; flag orphans; include sample rows in JSON. add `delete_unreferenced_canonicals.sql`. tests.
**Verification:** 12 passed.
**Outcome:** success

---

### [2026-04-20] — run_all generate rewrite dead-end quarantine continuation

**Context:** stop run on repeated `rewrite_prepare_round` failures.
**Happened:** confirm gap in `should_continue_after_quarantine(...)`. add `_is_generate_size_dead_end(...)` classifier. rule: continue on stable publishability dead ends (missing defs / incomplete evaluation). tests.
**Verification:** 45 passed.
**Outcome:** success
**Insight:** dead-end classification must cover all pipeline stages (inc. rewrite/publishability).
**Promoted:** yes

---

### [2026-04-20] — global temperature policy + generate rescue

**Context:** implement shared nonzero temp policy; pair-rating resilience; generate-time rescue.
**Happened:** shared temp helpers: floor `0.1`, 5-attempt ramp (`+0.025` to `+0.10`). audit log `llm_parse_failure`. tolerant JSON extraction. pair rating: parse-miss votes no longer terminal fail (accept `single_model_fallback` as complete). record metadata. early unresolved canonical fallback + DEX rescue in `run_all` generate. tests.
**Verification:** 170+ passed.
**Outcome:** success
**Insight:** unresolved-only generate rescue avoids false "incomplete" flags on fresh clues.
**Promoted:** yes

---

### [2026-04-21] — global top_p filter

**Happened:** add `GLOBAL_LLM_TOP_P = 0.95`. thread through streaming / fallback / retry paths. debug logging. tests.
**Verification:** 154 passed.
**Outcome:** success

---

### [2026-04-21] — publish runtime bypass + duplicate partial uploads

**Context:** `run_all` publish quarantine (NameError); duplicate puzzle rows on crash.
**Happened:** fix `definition_referee` loop variable rename bug. Fix bypass: instantiate `ClueCanonService` with shared runtime during upload. Move puzzle insert after canonical resolution. Add best-effort cleanup on failure. tests.
**Verification:** 150 passed.
**Outcome:** success
**Insight:** delay durable inserts until referee/canonical resolution finishes to prevent duplication.
**Promoted:** yes

---

### [2026-04-21] — run_all touched canonical cleanup

**Context:** delete newly created but unreferenced canonicals during `run_all`.
**Happened:** `delete_unreferenced_canonicals_by_ids(...)`: clear links + delete if ref=0. decision status `created-vs-reused`. simplify merges: delete superseded source ids. tests.
**Verification:** 48 passed.
**Outcome:** success
**Insight:** touched-only cleanup needs creation provenance on `CanonicalDecision`.
**Promoted:** no

---

### [2026-04-22] — run_all rejection/truncation diagnostics

**Context:** logging detail for rejections + truncations (Gemma speed investigation).
**Happened:** structured rejection details in `validate_definition_text_with_details()` (match token/stem, leak kind). warnings + audit events. LLM truncation log: reasoning, source, lengths, preview, tokens, context. Grouping in run summaries.
**Verification:** 166 passed.
**Outcome:** success

---

### [2026-04-22] — full pytest root-cause cleanup

**Happened:** Fix `retitle/batch.py` `multi_model` NameError. refresh prompt experiment anchors for `v3exp014`, `v6exp005` (match production text).
**Verification:** 690 passed.
**Outcome:** success

---

### [2026-04-22] — run_all generate duplicate clue identity fallback

**Context:** `missing definitions: IT` on duplicates; coordinate key collapsed Split clues.
**Happened:** add `WorkingClueRef` (direction, index, starts). switch fallback + tracking to exact refs. regressions for duplicate `IT` units + zero-position clues.
**Verification:** 138 passed.
**Outcome:** success
**Insight:** list-position identity required for clues; markdown rows lose coordinates after splitting.
**Promoted:** no

---

### [2026-04-23] — unreferenced canonical cleanup policy + verify format retry

**Context:** delete redundant unreferenced canonicals only; stop retitle no-op churn; Gemma verify truncations.
**Happened:** Add shared classification: referenced/singleton/best/redundant. fail/delete only redundant. wired redefine persistence cleanup. run-local retitle no-change deprioritization (stable-key ledger). Tighten verify prompts. add format retry for truncated/commentary outputs (preserve salvage).
**Verification:** 127 passed.
**Outcome:** success
**Insight:** verify truncations = format drift; improve retry shape + contract over raw budget increase.
**Promoted:** no

---

### [2026-04-24] — short-word definition rescue for IT/IJE/SEM

**Context:** truthful DEX ingestion; original-form preservation; additive overlays.
**Happened:** accept `defWrapper` + `span.def` in DEX parser. reparse cached `not_found` HTML. Fix Rust bridge: render `word.original` (preserve `iț` vs `IT`). Add `short_word_clues.json` overlays. add context + forbidden tokens to prompts. DEX-first rescue. benchmark dataset for fragile words.
**Verification:** 238 passed; dataset generated.
**Outcome:** success
**Insight:** Rust bridge rendered normalized to original; caused downstream form loss.
**Promoted:** yes

---

### [2026-04-24] — shared answer supply for curated short answers

**Context:** managed 2-letter answers separately; additive to grid gen + prompts. Review miner + eval roadmap.
**Happened:** Migrate overlay to `answer_supply.json`. Seed 41 county codes (`curated_ro_plate`) + variants + `IR`. Rust: read `clue_support_score` / `source`; score supported answers with lower penalty. Python: augmented words file for Rust. source-labeled prompt context. rescue as `answer_supply`. playful miner + eval helpers. `ROADMAP.md`.
**Verification:** 252 passed; cargo tests OK.
**Outcome:** success
**Insight:** metadata must travel to Rust for grid-gen benefit.
**Promoted:** no

---

### [2026-04-24] — ccTLD answer supply expansion

**Happened:** Add 247 `curated_cc_tld` factual entries from IANA. Add 46 prompt-only generic alternatives. generic tone ordering. tests.
**Verification:** 115 passed.
**Outcome:** success

---

### [2026-04-24] — roadmap gold corpus ingestion plan

**Happened:** Replace `ROADMAP.md` with ranked roadmap (DEX/answer-supply/eval). define 2-photo layout, JSONL shape, review UI workflow, promotion rules.
**Outcome:** success

---

### [2026-04-25] — architecture deepening candidate scan

**Happened:** audit codebase; synthesize deep-module refactor candidates: rewrite generation, canonical planning, pair assessment, attempt orchestration, search policy, puzzle session, task ports.
**Outcome:** success
**Insight:** `rewrite_engine.py` is compatibility surface; logic spans session/rounds modules.
**Promoted:** no

---

### [2026-04-25] — rewrite candidate generation interface design

**Happened:** constraints framing; four designs (minimal, strategy, round, ports). Convergence: extracted hydrated request/result types + candidate generator (hide gen-vs-rewrite, hybrid, validation, filtering, dispatch). scoring/alternation external. RFC issue #25.
**Outcome:** success
**Insight:** "hydrated context + model -> pending candidates" is safest boundary.
**Promoted:** no

---

### [2026-04-25] — canonical persistence planning interface design

**Happened:** inspect duplicated paths. Constraints: pre-insert resolution, dry-run planning, repair reuse, touched cleanup. Design converged on canonical input -> planned persistence boundary. resolver/payload/cleanup ports. RFC issue #26.
**Outcome:** success
**Insight:** planner expose touched ids; keep apply semantics outside (non-transactional resolution).
**Promoted:** no

---

### [2026-04-25] — triage TDD issues for rewrite/canonical RFCs

**Happened:** investigate paths with explorers. File TDD issues #27 (rewrite boundary), #28 (canonical planner). Root causes: semantics drift (standalone vs unattended); behavioral drift across repair/run_all/redefine/upload.
**Outcome:** success
**Insight:** repair canonical persistence lacks touched-id cleanup + ignores `multi_model=False`.
**Promoted:** no

---

### [2026-04-25] — markdown telegraph rewrite

**Happened:** Tighten prose across docs, agent guidance, prompt files, roadmap, review notes. Preserve code fences, placeholders, paths, links, Romanian prompts.
**Verification:** placeholder scan pass; MD-only changes.
**Outcome:** success
**Insight:** none
**Promoted:** no

---

### [2026-04-26] — run_all live log triage

**Happened:** Inspected active `build/run_all_runs/20260424_063040` while process running. Found healthy throughput/no failed jobs, but 3263 switches and ~8.1h activation+unload overhead. Recent rapid switches caused by publish canonical planner resolving clues serially; each single clue calls two-model `definition_referee` directly, outside scheduler batching.
**Outcome:** analysis only
**Insight:** canonical upload planning must batch referee decisions or expose them as scheduler-visible LLM units.
**Promoted:** yes

---

### [2026-04-26] — run_all egress + model thrash fixes

**Happened:** Implemented egress-safe restart guard; minimal-return Supabase writes; one redefine metadata update per job; narrow run_all poller reads; title-key cache; grid-size/simplify RPC paths; bulk canonical persistence planning; switch/egress/referee summary counters; SQL migration.
**Verification:** `pytest tests/generator/platform/test_supabase_ops.py tests/generator/workflows/canonicals/test_planner.py tests/generator/cli/test_loop_controller.py tests/generator/workflows/test_redefine.py tests/generator/workflows/test_upload_phase.py tests/generator/platform/test_clue_canon_store.py tests/generator/cli/test_run_all.py -q` -> 131 passed. `pytest tests/generator/workflows/test_retitle.py tests/generator/workflows/test_upload_phase.py tests/generator/cli/test_run_all.py -q` -> 84 passed. `rg` run_all broad-select scan -> no matches. `git diff --check` OK.
**Outcome:** success
**Insight:** Supabase mutations need explicit `ReturnMethod.minimal` when response rows unused.
**Promoted:** yes
