# Lessons Learned

> AI-maintained. validated reusable insights.
> **read start of task. update end of iteration.**

## How to Use

- **start of task:** read before code — avoid mistakes
- **end of iteration:** new reusable insight? → add to category
- **promotion:** 2+ occurrences in `ITERATION_LOG.md` → promote here
- **pruning:** obsolete → Archive section (date + reason). never delete.

---

## Architecture & Design Decisions

**[2026-03-14]** Two-model architecture prevents self-reinforcing hallucinations — single LLM rating own work creates echo chamber. Alternate `gpt-oss-20b` and `eurollm-22b` across rewrite rounds. Model B rates Model A. Cross-model verification breaks feedback loop.

**[2026-04-01]** Canonical-clue schema change requires unified compatibility layer for hydration/persistence — scattered `select`/`update` on legacy `crossword_clues.definition` increases migration risk. Implement adapter: resolve `canonical_definition_id` text first, fallback to legacy `definition` if exists, gate all writes through adapter-determined fields.

## Code Patterns & Pitfalls

**[2026-04-13]** LLM client streaming robustness for mocks — `_chat_completion_create_streaming` must handle `delta` (real stream) and `message` (non-streaming mock) choice fields. Process-wide streaming logic breaks tests if mocks return message-style objects. Support both in internal chunk parser to keep tests green, avoid mock churn.

**[2026-04-17]** Compound rebus clues must split before DEX caching/lookup — caching compound (e.g., `AURI - AMUS`) as `not_found` poisons subsequent `get()` calls. Expand compound clues into atom lookups before `prefetch`. `get()`/`lookup()` must combine atom results. Prevent fake DEX misses.

**[2026-03-14]** Short words (OU, AT, OF) special handling — 2-letter word definitions often leak answers. English homograph hints inject Romanian meaning. Preset definitions (AT, OF) bypass LLM. `_definition_describes_english_meaning()` guard rejects English-meaning definitions.

**[2026-04-06]** English-marker guards: tokenize normalized Romanian text, not raw ASCII spans — `[A-Za-z]+` scan splits diacritics into false English tokens (`forța` → `for` + `a`), rejecting valid Romanian. Normalize diacritics first, tokenize lowercase Latin. Separate cleanup for reasoning residue/translations.

**[2026-03-14]** Family check: prefix stripping required — `clue_uses_same_family` previously stripped only suffixes. Prefixed words (NEINCEPUT → ÎNCEPUT) missed. Implemented `ROMANIAN_PREFIXES`, `forbidden_definition_stems()`, `_family_exclusion_note()` in prompt builders.

**[2026-03-22]** Active clue auto-scroll gate: dedicated container required — `scrollIntoView()` on stacked/mobile layouts yanks page from grid. Auto-scroll only if clue pane scrollable. Pair grid focus change with `preventScroll` to stop cell focus jumps.

**[2026-03-20]** Production selection alignment with assessment — if benchmark ranks verified/exact clues first but production uses semantic+rebus totals, prompt experiments optimize wrong target. Update selector and rewrite gates together; retest.

**[2026-03-20]** `locked` state alignment — 9/8 clue with wrong guess might be skipped forever if lock logic only checks scores. Align lock semantics with `_needs_rewrite()`. Follow exact verification, not just thresholds.

**[2026-03-24]** Phase-1 variant pinning — grouping metadata by normalized word insufficient. If `word_type`/`word_original`/DEX context re-selected each access, clue drifts across pipeline steps. Resolve/pin one variant per clue once; carry through pipeline.

**[2026-03-30]** Rewrite loop structural-rejection channel — failures (e.g., `too short`, `single-word gloss`) vanish between rounds if used only for retries. Store last structural rejection on clue assessment; clear when valid candidate replaces clue. Failure synthesis: prefer verify/rating signals, fallback to structural reason.

## Testing & Quality
**[2026-03-18]** `rate_puzzle()` tests must mock `DexProvider.for_puzzle()` — prevents `tests/test_verify.py` hangs/env-dependence during prefetch. Unit tests for verify/rate must stub DEX access explicitly.

## Performance & Infrastructure
<!-- **[YYYY-MM-DD]** title — explanation -->

**[2026-03-23]** Native hot-path migrations: host-language contract stability — when moving slow phase to Rust, preserve Python-facing shape (`Candidate`, markdown, metadata hooks). Hide engine behind subprocess boundary. Use build-step entrypoint (e.g., `run_batch_loop.sh`) for fast failure on missing binaries.

## Dependencies & External Services
<!-- **[YYYY-MM-DD]** title — explanation -->

**[2026-04-02]** Supabase/PostgREST `in_()` calls: UUID sanitization required — malformed non-UUID in batched filter causes "Bad Request" (JSON-parse failure in Python client). Filter candidate IDs to valid UUIDs first. Prefer DB view over client-side follow-up fetches for partially migrated data.

**[2026-04-01]** High-volume two-model referee batching — alternating `primary -> secondary` per item causes LM Studio model thrashing. Collect bounded queue; run all primary votes, then all secondary votes. Isolate batch path to offline/high-volume jobs; keep live flows simple.

**[2026-03-30]** LM Studio `reasoning_effort` vs token budget — `medium` effort can consume entire `max_tokens` in `reasoning_tokens`. Legacy budgets (rewrite ~220, rate ~260) risk `finish_reason="length"` with empty output. Revalidate budgets per phase; log truncated completions with reasoning counts.

**[2026-04-04]** Resumable jobs: active work authoritative, pending queue advisory — active entries carry merge/progress state for safe resume. Pending queues stale quickly on filter/eligibility changes. Resume: keep valid active items, rebuild/sanitize pending words against fresh workset, add no-bucket guard to prevent crashes.

**[2026-04-04]** LM Studio reasoning policy contract — models advertise reasoning vocab in `/api/v1/models` (`off/on`) but `/v1/chat/completions` expects another (`none|minimal|low|medium|high|xhigh`). Centralize normalization; treat live endpoint acceptance as truth.

**[2026-04-04]** Resumable backfills: DB-filtered worksets + per-word persistence — migration reruns loading full corpus hide real bottlenecks. Filter source query to unresolved eligible rows. Prefetch canonicals in one query per wave; batch clue-link/alias writes per word. Skip finished words; cut DB round-trips.

**[2026-03-23]** LM Studio transport IDs vs audit labels — `display_name` for logs, `model_id` for API. Fallback to `"default"` or passing labels (e.g., `gpt-oss-20b`) where LM Studio expects downloaded ID causes 400/500 failures. Reconcile against `/api/v1/models`; pass explicit `model_id` every call.

**[2026-03-20]** Top-k verifier semantics — if verify returns multiple candidates, pass/fail, notes, metrics, and assessment must treat "any match" as success. Prevents data loss and benchmark drift.

**[2026-03-20]** DEX redirect definitions: robustness + expansion — `Diminutiv al lui fir.`, `Vezi X.`, or inline HTML risk parser drops. One-word redirects weak for context. Keep direct definition; add max one hop base-sense context; flag unresolved short cases.

**[2026-03-21]** DEX cache: gitignored local layer before Supabase — prevent Supabase hits for same words/redirects in local runs. Store `ok` and `not_found` locally; keep `get()`, `lookup()`, `prefetch()` cheap after first run.

**[2026-03-21]** DEX semantic expansion for short patterns — trigger expansion on `Diminutiv al lui X`, `Acțiunea de a (se) X`, `Faptul de a (se) X`, `Proprietatea de a fi X`, `A <ordinal> parte dintr-un/dintr-o X`, one-word synonyms (e.g., `Corabie.`). Strip punctuation/parentheticals; inject base lexeme sense.

**[2026-03-21]** Assessment dataset refresh — prefer provider lookup over `dataset.json` strings to avoid stale raw definitions in multistep assessment. Use old text only as fallback.

**[2026-03-21]** Top-k verifier rewrite support — rewrite prompts, failure-history, and synthesized reasons must use entire `verify_candidates` list. `wrong_guess` only for compatibility.

**[2026-03-20]** LM Studio unload: use `instance_id`, not model key — `/api/v1/models` exposes instances separately. Switching by key risks leaving old models loaded. Resolve active instance ID before unload.

**[2026-03-20]** "Publishable" exact-solve floor — gate publication on blocker-free state AND minimum verification pass rate. Prevents shipping weak pass-rate puzzles clearing loose score thresholds.

**[2026-03-20]** Rating model invalid JSON retry — low temperature local models still drift from schema. Re-ask explicitly for one JSON object only on parse failure to save limited attempts.

**[2026-03-20]** Metrics separation: first-pass vs final-pass — track `first_passed` and `final_passed` explicitly. Avoid fake `definition_first_pass_rate` from rewrite-loop-only verified counts.

**[2026-03-21]** Shared process logging: wrap stdout/stderr — immediate timestamped logs without big-bang migration. wrapper must be idempotent to prevent double-prefixing.

**[2026-03-21]** Prompt experiment runner input — consume machine-readable assessment artifacts, not TSV append order. Use TSV for history; use assessment JSON for keep/discard logic and control-word stability.

**[2026-03-29]** Prompt decision regime change — verifier/selection/runtime changes break direct comparability with old scores. Replicate incumbent-vs-candidate batches (`3 vs 3`). Use pass-rate + tier-balanced pass-rate for keep/discard.

**[2026-03-21]** Prompt campaign manifest anchor check — literal `find -> replace` skips experiments if text drifts. Regression test: load current prompts, assert manifest edit anchors exist before launch.

**[2026-03-21]** Benchmark incumbent source — source from `build/evaluation/assessment/results.tsv`, not code constants. Read latest kept row for numbers; use JSON for per-word drilldown.

**[2026-03-22]** Prompt experiment runner/test landing state — accept "replacement already present" as valid. Baseline absorbing cleanup edits shouldn't trigger CI failure. Treat original anchor or replacement text as acceptable; no-op cleanly.

**[2026-03-22]** Overnight prompt optimization state — externalize trial records, family stats, events to disk. Restore from incumbent snapshot on recovery, not conversational memory.

**[2026-03-22]** Durable-state rebuild: stage then swap — rebuilding in live dir risks total loss on failure. Rebuild in temp dir; atomically replace; refresh absolute snapshot paths.

**[2026-03-22]** Prompt autoresearch families: match hypothesis classes — split by experiment type (e.g., positive vs negative examples) to prevent broad-family early-stop from killing untested variants.

**[2026-03-23]** Fragile word watchlist — track recurring collateral losers (e.g., `AZ`, `MIRE`, `SAN`) distinct from controls. Stop research early on recurring regressions.

**[2026-03-23]** Maintenance-only autoresearch commands: side-effect free — `--rebuild-state`/`--status` must not launch assessments. Prevent accidental drift/wasted runs. Return immediately; lazy parent dir creation for audit writers.

**[2026-03-23]** Benchmark artifact location — save `best_assessment.json` etc. in `build/state/`, not in prompt source tree. Avoid committable clutter and diff confusion.

**[2026-03-23]** Autoresearch rebuild refresh — must refresh snapshot paths AND live prompt tree after swap. Prevents false incumbent mismatch reports on next validator run.

**[2026-03-23]** Supabase clue direction codes — accept `H`/`V` (persisted format) in DB-to-state adapters. Avoid routing all clues to horizontal by only checking `"vertical"`.

**[2026-03-24]** Phase-1 size tuning: dictionary length histograms — slot-capacity floors/black density must react to long-word scarcity (e.g., `8-letter = 12481` vs `15-letter = 546`). Formula should account for histogram pressure.

**[2026-03-26]** Title screening: separate rejection reasons — structured result (`title`, `valid`, `feedback`, `score`) required. Prevents immediate mapping to random fallback; enables deterministic `0` scores and precise rejection context.

**[2026-03-26]** Multi-model title loops: JIT activation — prebuilding alternating list unloads generator before call. Keep rejected-history/hints scoped per generator; prevent empty-output retries polluting context.

**[2026-04-04]** LM Studio reasoning controls per model/purpose — central registry must handle model differences (`on/off` vs `low/med/high` vs omit field). `None` = omit parameter entirely.

**[2026-04-05]** Local LLM referee calls: bounded classification — disable reasoning for `clue_compare` micro-tasks. Strict JSON contract; cap completion budget (double/triple digit tokens) to avoid long "thinking" traces.

## Process & Workflow
**[2026-04-17]** Audit dedupe key: include run ID — prevents cross-cache suppression bursts. Fallback to per-instance behavior when no run context active.

**[2026-04-17]** Heartbeat summaries: separate snapshot writer — periodic state files for long runs; human summary only at end. Build payload helper; write snapshots on timer.

**[2026-04-03]** Final storage cutover audit gate — require proof of coverage (`NULL` pointers = 0, legacy reads/writes = 0) before dropping column. Prevent follow-up cleanup migrations.

**[2026-04-05]** Staged schema cutover: replace views before drop — finalize migration fails if column dropped before view updated. Reorder: `replace view` → `drop column`. Avoid `CASCADE` unless dependents enumerated.

**[2026-04-03]** Standardized human progress output — route library/progress chatter through `runtime_logging.log()`. `install_process_logging()` on entrypoints. Reserve `print()` for logging primitive or stdout scripts.

**[2026-04-05]** Timestamped run-log teeing: flush fragments — avoid newline buffering to show live token-by-token reasoning/content in `run.log`. Keep prefix stateful per line.

**[2026-04-01]** macOS Bash 3.2 empty array expansion — `args=("$@")` with `"${args[@]}"` throws `unbound variable` on `set -u` if empty. Scan `"$@"` directly; branch on `$#`.

**[2026-03-18]** Prompt experiment rollback — `run_assessment.py` always appends to TSV. Hill-climber must snapshot/restore TSV for discarded runs. Campaign resets/isolation required to avoid skipping names.

**[2026-03-18]** Mid-experiment interruption recovery — compare `prompts/production/` against campaign backup before trusting tree. Current files might have unreviewed edits.

**[2026-03-18]** Isolated experiment logs — use monolithic campaign log only for summaries. Separate `expNNN.log` per assessment run for verbosity management.

**[2026-03-20]** Git commit vs prompt state — results TSV for score history, not authoritative prompt state. Interrupted runs/ignored logs desynchronize history. Restore/diff prompt backups explicitly.

**[2026-03-30]** Size balancing from live inventory — use Python loop controller to query `crossword_puzzles` counts. Target missing/scarce sizes instead of static `7..15` loop.

**[2026-04-05]** Model residency cost in multi-model referee — throughput poison if phases shrink to 1-3 requests while switching models. Prefer whole-batch model phases; use hard-bounded classifiers for tiny JSON tasks.

**[2026-04-06]** Overthinking fallbacks in shared helper — if `finish_reason="length"` occurs after zero visible output (all thinking), retry centrally with `reasoning_effort="none"`. Trigger on `reasoning_tokens` presence. DRY behavior across all flows.

**[2026-04-05]** Cache-backed canonical insert recovery — read-then-insert unsafe. On `23505` (conflict), invalidate cache, refetch by identity, reuse/bump row.

**[2026-04-05]** Backfill "eligible rows" definition — separate "verified rows for merges" from "all rows needing canonical pointer". Avoid misleading `0` counts by matching cutover invariant.

**[2026-04-05]** Puzzle clue-integrity audit — check structural truth + bulk reads. Fetch metadata once, clues in puzzle-id chunks. Derive slots from `grid_template`; compare client-side.

**[2026-04-05]** Metadata preservation across serialization — bridge objects (`WorkingClue` -> export -> upload) must carry all fields (e.g., `word_type`). Avoid late-stage `AttributeError` after long runs.

**[2026-04-07]** Transition-era code deletion — once invariant met, delete compatibility flags/branches. Collapse runtime to steady-state schema. Migration history in SQL/docs only.

**[2026-04-07]** Two-model scoring layers — keep low-level helpers (`rate_definition`) simple ("one request in, one parsed vote out"). Put consensus/batching in phase/runtime layer (`verify`, `theme`).

**[2026-04-07]** Model-specific activation hooks — orchestrators need stable seams. Call `activate_primary()`/`activate_secondary()` directly in tests; accept duck-typed descriptors. Avoid generic `ensure_active()` to keep mocks working.

**[2026-04-07]** Shared dispatch helper — mandatory entrypoint above request helper. Production phases submit work items here. Centralizes switch-drift management, warm-up hacks, and batching.

**[2026-04-07]** Empty-only model phase admission freeze — prevent starvation by freezing new admissions once both model queues non-empty. Drain current admitted queue before switch.

**[2026-04-07]** Supervisor orchestration unit — job-state must be resumable per-topic. Global pending queue with single active job hides real concurrency.

**[2026-04-07]** CLI runner vs Supervisor step — `run_all` production entrypoint must not call helpers owning `SystemExit`, signal handlers, or sleep loops. Use pure primitives; treat `SystemExit` as boundary violation for topic job.

**[2026-04-07]** Separate worker lane for non-LLM prep — move CPU-bound phases (Rust/grid fill) to background worker. Prevent local prep from blocking the serialized LLM lane.

**[2026-04-07]** Helper split: `prepare_*` vs `apply_*` — split mixed helpers into LLM-allowed plan generation and pure persistence. Supervisor schedules as separate stages.

**[2026-04-09]** Pair-eval short-circuit assumptions — finalizers must iterate over present votes. `votes[model_id]` lookup after short-circuit risks crash if second vote missing.

**[2026-04-09]** Unattended failure memory — quarantine `(topic, stage, error signature)` after 3 retries. Keep failure ledger; emit heartbeats; back off unhealthy targets.

**[2026-04-09]** Opt-in LLM budget policy — caps/downgrades valuable for `run_all` but can distort CLIs/tests. shared helper capable of injection; enable explicitly from supervisor.

**[2026-04-09]** Compatibility facade patch surfaces — `from X import *` drops underscores and leaves globals bound to original. Alias module identity directly for legacy modules that tests patch.

**[2026-04-09]** `sitecustomize.py` precedence — global/vendor hook might win. Re-bootstrap package paths in inner `__init__.py`/`__main__.py`.

**[2026-04-09]** Repo-root import bootstrap — tiny root package setting `__path__` to `packages/.../src` is better than alias trees for resolving `rebus_generator` from root.

**[2026-04-10]** Deterministic size dead-ends — quarantine size choice, not whole run. Selection dead-ends (unsat grid, unpublishable results) cooled down. Whole-run quarantine only for logic regressions.

**[2026-04-10]** Facade refactor trap — bidirectional facades cause unclear ownership and broken patches. Pick one owner; keep facade explicit/one-way; add contract tests.

**[2026-04-10]** Shared-state constructor prerequisites — helpers delegating to new shared objects shouldn't be called during object construction. Seed from raw state; switch after init.

**[2026-04-10]** Malformed payload truncation fallback — `finish_reason="length"` with partial JSON/choice is failure. Trigger no-thinking retry if content unusable, not just if blank.

**[2026-04-10]** Seed-owned selector randomization — `random.choice()` breaks reproducibility. Use stable RNG helper seeded from run/item identity.

**[2026-04-10]** Template pruning alignment — align generator-time placement guards with final validation slot policy. Prevents false search-budget failures.

**[2026-04-10]** Search benchmark stats separation — accumulate solver node totals across attempts; keep structural quality metrics (e.g., black count) specific to selected candidate.

**[2026-04-10]** Base vs Tuned black targets — distinguish base target from `tune_settings_for_dictionary()` scarcity bonus in logs. Prevents misreading retune impacts.

**[2026-04-11]** Dictionary scarcity profile sidecar — build `words.profile.json` from Rust filter semantics. Use for effort scaling/heuristics; align with phase-1 word rows.

**[2026-04-11]** Resettable quality fields as cache — verified/rate results must stay in-memory truth for rewrite. Neutral deterministic ordering for blank canonical evidence.

**[2026-04-11]** Ready-unit cycle dedupe — suppress rerunning unchanged `(job_id, step_id, phase)` within one supervisor pass to avoid "drain model" infinite loops.

**[2026-04-11]** Session finish contract — `RunAllRewriteSession` must satisfy `finish_rewrite_session()` expectations (e.g., `.final_result`). Avoid mixing helper APIs across session types.

**[2026-04-18]** Short-word leakage guard — `clue_uses_same_family()` ignores roots <4 chars. Add strict 2-3 letter rejection at clue-validation layer. Prefer false rejects over leaks.

**[2026-04-20]** Same-text canonical repair — rehydrating from scored canonical unblocks publishability even if text identical. Scope to unresolved generate clues only.

**[2026-04-20]** Early vs Late rescue policy — `generate_scored_fallback_policy()` too broad for pre-rewrite. Use unresolved-only policy for early define-finalize rescue.

**[2026-04-20]** Reasoning transport: intent vs params — separate abstract `reasoning_enabled` from `reasoning_effort` param. Carry flag through budgeting/retries to correctly handle omitted-param thinking.

**[2026-04-21]** Publish flow ordering — resolve canonicals/referees before inserting `crossword_puzzles` row. Prevents duplicate orphans on late-stage exceptions.

**[2026-04-24]** Normalized vs Original form preservation — Rust payloads carry both. Bridge must render `word.original` to markdown; use `normalized` for grid/verify.

**[2026-04-24]** Curated short-answer dictionary support — materialise curated fallback definitions as Rust word rows for grid use, not just Python rescue context.

---

## Archive
<!-- **[YYYY-MM-DD] Archived [YYYY-MM-DD]** title — reason -->

### Python Dependency Management and Execution
**Lesson:** Monorepo Python projects: `uv` with root `pyproject.toml` reduces friction. Configure build backend (e.g., `hatchling`) to `src` dirs. `uv sync` installs editable mode automatically. Replaces `PYTHONPATH` hacks, manual `venv`, and `sitecustomize.py` injections. Unifies local dev and GitHub Actions via `astral-sh/setup-uv`.
