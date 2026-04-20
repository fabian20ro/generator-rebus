# Lessons Learned

> maintained by AI agents. validated, reusable insights.
> **read start of every task. update end of every iteration.**

## How to Use

- **start of task:** read before writing code — avoid known mistakes
- **end of iteration:** new reusable insight? → add to appropriate category
- **promotion:** pattern 2+ times in `ITERATION_LOG.md` → promote here
- **pruning:** obsolete → Archive section (date + reason). never delete.

---

## Architecture & Design Decisions

**[2026-03-14]** Two-model architecture prevents self-reinforcing hallucinations — when same LLM rates its own definitions, it agrees with itself. Alternate gpt-oss-20b and eurollm-22b across rewrite rounds. Model B rates Model A's work. Cross-model verification broke the feedback loop.

**[2026-04-01]** Canonical-clue schema changes need one compatibility layer for clue text hydration and persistence — once `crossword_clues.definition` becomes legacy, scattered `select(...definition...)` and `update({"definition": ...})` calls turn every flow into a migration risk. Keep one adapter that resolves effective clue text from `canonical_definition_id` first, falls back to legacy `definition` only when that column still exists, and makes every writer ask the adapter which fields to materialize.

## Code Patterns & Pitfalls

**[2026-04-13]** LLM client streaming robustness for mocks — `_chat_completion_create_streaming` should handle both `delta` (real streaming) and `message` (non-streaming mock) choice fields. Forcing streaming logic process-wide for consistency/reasoning capture can break existing test suites if their mocks return message-style objects. Support both in the internal chunk parser to keep tests green without massive mock churn.

**[2026-04-17]** Compound rebus clues must split before DEX caching or lookup, not after a `not_found` write — if a compound like `AURI - AMUS` is cached as a whole and marked missing, later `get()` calls on that same clue will short-circuit on the poisoned `None` entry. Expand compound clues into atom lookups before `prefetch`, and make direct `get()`/`lookup()` combine atom results so the whole clue never becomes a fake DEX miss.

**[2026-03-14]** Short words (OU, AT, OF) need special handling — for 2-letter words, any definition almost inevitably contains the answer. English homograph hints inject correct Romanian meaning. Preset definitions (AT, OF) bypass LLM entirely. `_definition_describes_english_meaning()` guard rejects English-meaning definitions.

**[2026-04-06]** English-marker guards must tokenize normalized Romanian text, not raw ASCII spans — scanning `[A-Za-z]+` on live clue text turns diacritic words into false English tokens (`forța` → `for` + `a`) and silently rejects valid Romanian definitions. Normalize diacritics first, then tokenize lowercase Latin words; keep cleanup separate for reasoning residue and inline English translations.

**[2026-03-14]** Family check needs prefix stripping — `clue_uses_same_family` only stripped suffixes. Prefixed words (NEINCEPUT→ÎNCEPUT) weren't caught. Added `ROMANIAN_PREFIXES` list, `forbidden_definition_stems()`, and `_family_exclusion_note()` in prompt builders.

**[2026-03-22]** Auto-scrolling the active clue must be gated by a dedicated clue scroll container — on stacked/mobile crossword layouts, `scrollIntoView()` on the active clue can yank the whole page away from the grid every time selection changes. Only auto-scroll when the clue pane itself is scrollable, and pair grid focus changes with `preventScroll` so cell focus does not trigger another jump.

**[2026-03-20]** Keep production selection aligned with assessment selection — if the benchmark ranks verified/exact clues first but production still prefers semantic+rebus totals, prompt experiments optimize the wrong target. Change selector and rewrite gates together, then retest.

**[2026-03-20]** `locked` state must follow exact verification, not only score thresholds — a 9/8 clue that still guesses wrong can get skipped forever in rewrite rounds if lock logic only checks semantic/rebus. Keep lock semantics aligned with `_needs_rewrite()`.

**[2026-03-24]** If phase-1 fills only normalized answers, later stages must pin one concrete variant per clue before generation starts — grouping metadata by normalized word is not enough. If `word_type` / `word_original` / DEX context are re-selected on each access, the same clue can drift across define/verify/rewrite/title steps. Resolve one variant once per puzzle clue, then carry that pinned choice through the rest of the pipeline.

**[2026-03-30]** Rewrite loops need a persistent structural-rejection channel separate from evaluation failure reasons — internal rewrite validation failures like `too short`, `single-word gloss`, or `dangling ending` vanish between rounds if they are only used for same-call retries. Store the last structural rejection on the clue assessment itself, clear it when a valid candidate replaces the clue in the same round, and let failure synthesis prefer verify/rating signals before falling back to that structural reason.

## Testing & Quality
**[2026-03-18]** `rate_puzzle()` tests must mock `DexProvider.for_puzzle()` — otherwise `tests/test_verify.py` can hang or become environment-dependent during DEX prefetch. Unit tests for verify/rate flow should stub DEX access explicitly.

## Performance & Infrastructure
<!-- **[YYYY-MM-DD]** title — explanation -->

**[2026-03-23]** Native hot-path migrations are safest when the host-language contract stays stable — if a slow phase moves to Rust, keep the Python-facing result shape (`Candidate`, markdown, metadata hooks) intact and hide the new engine behind one thin subprocess boundary. Pair that with an entrypoint build step (`run_batch_loop.sh` / equivalent) so missing binaries fail fast before the long-running pipeline starts.

## Dependencies & External Services
<!-- **[YYYY-MM-DD]** title — explanation -->

**[2026-04-02]** Supabase/PostgREST `in_(uuid_column, ids)` calls need UUID sanitization before request build — one malformed or stale non-UUID id in a batched `.in_()` filter can come back as a bare `Bad Request` body, which the Python PostgREST client surfaces as a JSON-parse failure instead of a useful row-level error. Filter candidate ids to valid UUIDs first, and prefer a DB view over client-side follow-up fetches when the ids came from partially migrated data.

**[2026-04-01]** High-volume two-model referee passes should batch comparisons by model, not finish one item at a time — if a backfill/inference loop alternates `primary -> secondary` for every single comparison, LM Studio spends most of the run unloading and reloading models. Keep the 6-vote semantics, but collect a bounded queue of comparison requests, run all primary votes first, then all secondary votes, and isolate that batching path to offline/high-volume jobs so live single-item flows stay simple.

**[2026-03-30]** LM Studio `reasoning_effort` needs completion-budget retuning, not just a flag flip — on local `gpt-oss` runs, enabling `reasoning_effort="medium"` can spend most or all `max_tokens` inside `completion_tokens_details.reasoning_tokens`. Small legacy budgets that were safe before reasoning support (`rewrite` ~220, `rate` ~260) can then terminate with `finish_reason="length"` and empty visible output / missing JSON. When LM Studio server support changes, revalidate token budgets per phase and log truncated completions with reasoning-token counts.

**[2026-04-04]** Resumable jobs should treat serialized active work as authoritative and serialized pending queues as advisory — active entries usually carry enough merge/progress state to finish safely after resume, but pending queues can go stale as soon as source filtering or eligibility logic changes. On resume, keep valid active items, rebuild/sanitize pending words against the fresh workset, and add a final no-bucket guard so stale queue entries cannot crash the run.

**[2026-04-04]** LM Studio reasoning policy must follow the OpenAI-compatible endpoint contract, not `/api/v1/models` capability metadata — a model can advertise one reasoning vocabulary there (`off/on`) while `/v1/chat/completions` only accepts another (`none|minimal|low|medium|high|xhigh`). Keep reasoning normalization centralized, validate values before sending, and treat live endpoint acceptance as the source of truth.

**[2026-04-04]** Resumable backfills need DB-filtered worksets and per-word batch persistence — if a migration rerun still loads the full clue corpus, per-word queue/batch knobs only mask the real bottleneck. Filter the source query to unresolved eligible rows (`verified=true`, canonical pointer null, optional target word), prefetch existing canonicals for each queue-fill wave in one query, and batch clue-link + alias writes per committed word. Keep LM calls sequential if needed for local stability; most of the win comes from cutting DB round-trips and skipping already-finished words.

**[2026-03-23]** LM Studio orchestration must separate transport ids from audit labels and forbid implicit model routing — `display_name` is for logs/metrics, `model_id` is for API calls. If generation/title/tiebreak code falls back to `"default"` or passes labels like `gpt-oss-20b` where LM Studio expects the exact downloaded id, live state drift turns into hard-to-reproduce 400/500 load failures. Keep one runtime that reconciles against `/api/v1/models`, and pass explicit `model_id` on every chat completion call.

**[2026-03-20]** Top-k verifier changes need pipeline-wide semantics, not just a prompt tweak — if verify starts returning 2-3 candidates, pass/fail, exported notes, metrics, and assessment all need to treat “any candidate matches” as success. Otherwise near-miss data is lost and benchmark semantics drift from production.

**[2026-03-20]** DEX redirect-style definitions need both parser robustness and one-hop expansion — dexonline often returns entries like `Diminutiv al lui fir.` or `Vezi X.`; inline HTML tags can make a naive parser drop them entirely, and even parsed correctly they are too weak for prompt context unless the base lexeme's sense is also injected. Keep the direct definition, add at most one hop of base-sense context, and flag short unresolved cases separately.

**[2026-03-21]** DEX cache flow should include a gitignored local disk layer before Supabase — repeated local runs should not hit Supabase for the same words, and redirect dereference lookups should reuse the same local cache too. Store both `ok` and `not_found` results locally so `get()`, `lookup()`, and `prefetch()` all stay cheap after the first run.

**[2026-03-21]** Short first-definition DEX patterns are worth semantic expansion when they expose a clear base lexeme — beyond `Diminutiv al lui X`, useful families are `Acțiunea de a (se) X`, `Faptul de a (se) X`, `Proprietatea de a fi X`, `A <ordinal> parte dintr-un/dintr-o X`, and one-word synonym glosses like `Corabie.`. Use the first parsed definition as the trigger, strip trailing punctuation/parenthetical sense markers from the target, then inject the base lexeme's sense alongside the original definition.

**[2026-03-21]** Assessment datasets should refresh DEX text from the live provider, not trust old `dataset.json` strings forever — otherwise prompt/runtime code may be using improved expanded DEX context while multistep assessment still feeds stale raw definitions. Prefer provider lookup over reused dataset values, and only reuse the old text as fallback.

**[2026-03-21]** Top-k verifier support is incomplete if rewrite still sees only the first wrong guess — once verification returns multiple candidates, rewrite prompts, failure-history prompts, and synthesized failure reasons should use the whole candidate list. Keep `wrong_guess` only as a compatibility field; the richer signal is `verify_candidates`.

**[2026-03-20]** LM Studio unload calls must use loaded `instance_id`, not model key — `/api/v1/models` exposes loaded instances separately from model keys, and switching by key can silently leave the old model loaded. In two-model workflows, always resolve the active instance id before unloading.

**[2026-03-20]** “Publishable” needs an exact-solve floor, not only “no blockers” — otherwise puzzles with weak multistep pass rates can still ship just because every clue cleared loose score thresholds. Gate publication on both blocker-free state and a minimum verification pass rate.

**[2026-03-20]** Invalid JSON from the rating model needs a stricter retry prompt, not a blind resend — LM Studio-compatible local models can drift out of schema even with low temperature. On parse failure, re-ask explicitly for one JSON object only; otherwise reruns waste one of the limited attempts.

**[2026-03-20]** First-pass and final-pass metrics must be stored separately — if the rewrite loop only returns final verified counts, any reported `definition_first_pass_rate` is fake and churn analysis becomes misleading. Track `first_passed` and `final_passed` explicitly in prepared puzzles and metrics.

**[2026-03-21]** Shared process logging should wrap stdout/stderr instead of rewriting every `print()` call at once — this gives timestamped logs everywhere immediately, avoids a risky big-bang migration, and still allows gradual promotion of high-value signals to structured audit events. Make the wrapper idempotent so already timestamped child-process lines are not prefixed twice.

**[2026-03-21]** Prompt experiment runners should consume machine-readable assessment artifacts, not infer truth from shared TSV append order — TSV is fine as score history, but keep/discard logic needs per-tier/control data and should read a JSON artifact produced by assessment directly.

**[2026-03-29]** Once benchmark semantics change, prompt decisions need replicated incumbent-vs-candidate batches under the new regime — dataset rebuilds, verifier semantics, selection rules, or reasoning/runtime changes break direct comparability with older headline scores. Keep historical `results*.tsv` as evidence, but run at least `3 vs 3` replicated comparisons and use pass-rate plus tier-balanced pass-rate as the keep/discard basis.

**[2026-03-21]** Prompt campaign manifests need an anchor-existence test against live prompt files — literal `find -> replace` runners will silently skip experiments when prompt text drifts. Keep one regression test that loads current prompt files and asserts every manifest edit anchor still exists before launching a long campaign.

**[2026-03-21]** Benchmark incumbent metrics should stay sourced from `build/evaluation/assessment/results.tsv`, not duplicated in policy constants — the ledger row is the working score history, while code should only keep labels, ranges, and rules. Read the latest kept row when you need the incumbent numbers; use assessment JSON only for per-word drilldown such as control-word stability.

**[2026-03-22]** Prompt experiment runners/tests should accept “replacement already present” as a valid already-landed state — when baseline prompt text absorbs a prior cleanup edit, strict “find anchor must exist” checks create false CI failures even though the manifest is semantically aligned. Treat either the original anchor or the replacement text as acceptable, and have apply logic no-op cleanly when the replacement is already present.

**[2026-03-22]** Overnight prompt optimization must externalize all state — chat context and sub-agents are not durable enough for long campaigns. Store incumbent snapshot, trial records, family stats, and replayable events on disk; recovery should restore prompts from the incumbent snapshot, not from conversational memory.

**[2026-03-22]** Durable-state rebuilds should be staged in a temporary directory, then swapped in only after success — deleting the live state dir before replay/bootstrap finishes can turn a recoverable inconsistency into total state loss. Rebuild off to the side, then atomically replace the durable state and refresh any stored absolute snapshot paths.

**[2026-03-22]** Prompt autoresearch families must match hypothesis classes, not broad file blocks — early-stop on a coarse family like `definition_examples` can wrongly kill untested positive-example or guidance variants just because negative-example edits failed first. Split families by experiment type before enabling stale-family stop logic.

**[2026-03-23]** Repeated collateral losers deserve their own watchlist, distinct from high-tier controls — a prompt change can keep headline controls stable while still repeatedly breaking the same medium/common words (`AZ`, `MIRE`, `SAN`, etc.). Track those fragile words explicitly in classifier and family-stop logic so prompt research stops on recurring regressions earlier.

**[2026-03-23]** Maintenance-only autoresearch commands must stay side-effect free — flags like `--rebuild-state` and `--status` are recovery tools, not trial launchers. If a repair/status path can also start assessments, a “safe resume” tool becomes a source of accidental prompt drift and wasted benchmark runs. Return immediately after rebuild/status work, and make audit writers create parent directories lazily so these maintenance paths also work in isolated temp-state tests.

**[2026-03-23]** Benchmark runner state artifacts should live under a gitignored build/state root, never beside live prompt source — files like `best_assessment.json` are experiment cache, not prompt inputs. If they sit under `packages/rebus-generator/src/rebus_generator/prompts/production/`, they look committable and can confuse prompt diffs. Save them under `build/` and keep only read-only fallback support for legacy locations.

**[2026-03-23]** Autoresearch rebuilds must refresh both snapshot paths and the live prompt tree after swapping temp state into place — if `seed_prompt_snapshot` still points at the temporary rebuild dir, or if `packages/rebus-generator/src/rebus_generator/prompts/production/` is not restored from the rebuilt incumbent snapshot, the next validator run reports a false incumbent mismatch even though the durable state itself is correct.

**[2026-03-23]** Supabase clue directions must accept persisted `H`/`V` codes, not only spelled-out names — upload stores `crossword_clues.direction` as `H`/`V`, so any DB-to-working-state adapter that only checks `"vertical"` silently routes every clue into the horizontal list. Treat both compact DB codes and verbose strings as valid inputs before running redefine/repair flows.

**[2026-03-24]** Crossword phase-1 size tuning should derive from dictionary length histograms, not board size alone — the usable normalized-word buckets peak around medium lengths and thin sharply at the long end (for example current corpus: `8-letter = 12481`, `15-letter = 546`). Large-grid black density, template budget, and slot-capacity floors should react to that long-word scarcity; a smooth size formula helps readability, but top-end failures can still come from template/search topology after the histogram pressure is accounted for.

**[2026-03-26]** Title screening should separate invalid-candidate reasons from fallback selection — if sanitization immediately maps bad title outputs to a random fallback, retitle/publish flows lose the reason (`ALL CAPS`, too many words, leaked solution word), cannot assign deterministic `0` scores, and cannot feed precise rejection context into later rounds. Keep a structured title-review result (`title`, `valid`, `feedback`, `score`) and reserve fallback titles for the final “no valid candidate survived” path only.

**[2026-03-26]** Multi-model title loops must activate models just-in-time, not prebuild an alternating model list by calling activators up front — eager activation unloads the first generator before its own API call, produces misleading `[model]` logs, and turns valid orchestration bugs into fake prompt-quality failures. Keep rejected-history and corrective hints scoped per generator, and do not let empty-output retries pollute semantic rejection context.

**[2026-04-04]** LM Studio reasoning controls must be modeled per model and per purpose, with explicit omission support — a single global `reasoning_effort` assumption breaks as soon as one model expects `on/off`, another expects `low/medium/high`, and a third errors if the field is present at all. Keep reasoning policy in the central model registry, let each purpose fall back to a per-model default, and treat `None` as “omit this parameter entirely.”

**[2026-04-05]** Local LLM compare/referee calls must be treated as bounded classification, not open-ended reasoning — for tiny JSON decisions like `clue_compare`, letting Gemma run with reasoning enabled and `max_tokens` in the thousands turns simple pair judgments into long “thinking” traces that dominate end-to-end backfill time. Keep compare prompts on the strictest possible JSON contract, disable reasoning for that purpose, and cap completion budgets to a low triple- or double-digit token range.

## Process & Workflow
**[2026-04-17]** Run-local audit dedupe should key off the run id, not just the provider instance — provider-scoped suppression still repeats the same `dex_short_definition_detected` burst across recreated caches. Keep a run-id aware dedupe set, but fall back to per-instance behavior when no run context is active so tests and ad-hoc helpers do not leak state across calls.

**[2026-04-17]** Heartbeat summaries need a snapshot writer separate from the final log line — periodic machine-readable state files are useful during long unattended runs, but the final human summary should stay a one-off. Build one payload helper, write snapshots on a timer, and reserve the closing log for the final status line.

**[2026-04-03]** Final storage cutovers need an explicit audit gate, not just a migration script — before dropping a compatibility column, require one automated check that proves coverage (`NULL` pointer count = 0, effective-view legacy fallbacks = 0, production code has no direct legacy-column reads/writes). Without that gate, “one last migration” turns into follow-up cleanup migrations after the supposedly final cutover.

**[2026-04-05]** Compatibility views must be replaced before dropping the legacy columns they still reference — in a staged schema cutover, the “finalize” migration can fail even after audit passes if it drops a column before `CREATE OR REPLACE VIEW` removes that dependency. Prefer dependency-preserving reorder (`replace view` → `drop column`) over `DROP COLUMN ... CASCADE`, and only consider `CASCADE` after enumerating downstream dependents you are prepared to recreate.

**[2026-04-03]** Repo-wide human progress output should go through `runtime_logging.log()`; leave raw `print()` only inside the logging primitive itself or in deliberate stdout-contract scripts — mixed ad-hoc prints make timestamps inconsistent, break run-log teeing, and make long LM Studio jobs harder to debug. Standardize entrypoints on `install_process_logging()` and route library/progress chatter through `log()`.

**[2026-04-05]** Timestamped run-log teeing must emit partial fragments immediately when debug streaming matters — a newline-buffered wrapper is fine for coarse progress logs but silently hides token-by-token `reasoning_content` / `content` until the line closes. If LM traces should be visible live in `run.log`, keep the timestamp prefix stateful per line and flush fragments as they arrive instead of buffering the whole line.

**[2026-04-01]** `set -u` shell wrappers must not expand empty Bash arrays on macOS bash 3.2 — patterns like `args=("$@")` followed by `"${args[@]}"` can still throw `unbound variable` when no CLI args were passed. Scan `"$@"` directly, branch on `$#`, and only materialize/expand an array after guaranteeing at least one element.

**[2026-03-18]** Prompt experiment runs must roll back assessment artifacts on discard — `run_assessment.py` always appends to the assessment results TSV, so an outer hill-climber cannot trust "last row = current best" unless it snapshots and restores the TSV for discarded or interrupted experiments. Experiment logs also need per-campaign isolation or reset support, otherwise reruns silently skip prior experiment names.

**[2026-03-18]** Interrupted prompt campaigns can leave prompt files mid-experiment — if a run stops due to power loss or crash, compare `packages/rebus-generator/src/rebus_generator/prompts/production/` against the campaign backup dir before trusting the working tree. The current prompt files may contain the next experiment's unreviewed edit even when no result was recorded.

**[2026-03-18]** One log per experiment beats one monolithic campaign log — full multistep assessments are too verbose to share a single append-only file. Use a campaign JSON/TSV for summaries and a separate `expNNN.log` file for each assessment run.

**[2026-03-20]** Live git experiment commits are not enough to reconstruct winning prompt state — when the runner commits prompt edits before assessment and later tries to commit results, ignored log paths or interrupted runs can desynchronize git history, prompt backups, and the results TSV. Treat the results TSV as score history, not as authoritative prompt-state history; restore or diff prompt backups explicitly before starting the next campaign.

**[2026-03-30]** Overnight size balancing should come from live inventory, not a blind fixed loop — if the goal is to keep puzzle counts even across `grid_size`, a static `7..15` loop overproduces already-abundant sizes. Put the balancing logic in the Python loop controller, query current `crossword_puzzles` counts, treat missing sizes as zero, and let the shell entrypoint only enable that mode.

**[2026-04-05]** LM Studio multi-model referee design must match model residency cost — with one active loaded model at a time, a many-phase voting schedule can look statistically careful but become throughput poison once later phases shrink to 1-3 requests. Prefer whole-batch model phases with terminal decisions after one valid vote per model, and treat tiny-schema JSON compare prompts as hard-bounded classifiers, not explanation tasks.

**[2026-04-06]** Overthinking fallbacks belong in the shared chat-completion helper, not in per-feature branches — when local reasoning models hit `finish_reason="length"` after spending essentially the whole completion budget in thinking and producing no visible output, retry once centrally with `reasoning_effort="none"` and a short bounded budget. Trigger that fallback from the response itself (`reasoning_tokens` / hidden reasoning text), not from whether the original request explicitly sent `reasoning_effort`; omitted request params can still resolve to model-default thinking on the server. Keeping that policy inside the common helper preserves DRY behavior across generate/rewrite/verify/title flows and prevents future direct callsites from drifting.

**[2026-04-05]** Cache-backed canonical inserts need unique-conflict recovery — read-then-insert against `canonical_clue_definitions` is not safe under stale cache or concurrent runs. On `23505`, invalidate the word cache, refetch by canonical identity, and reuse/bump the existing row instead of crashing unattended backfills.

**[2026-04-05]** Backfill “eligible rows” must match the cutover invariant being measured — if cutover requires `crossword_clues.canonical_definition_id IS NULL = 0`, a backfill restricted to `verified=true` will misleadingly report `eligible_rows=0` while thousands of unverified null pointers remain. Separate “verified rows allowed to drive merges” from “all rows that still need a canonical pointer”.

**[2026-04-05]** Puzzle clue-integrity audits should use structural truth plus bulk reads — if the goal is “UI-safe puzzle load,” checking only blank definitions misses missing-slot rows, duplicates, and orphan clues. Fetch puzzle metadata once, fetch clue rows in bulk by puzzle-id chunks, derive expected slots from `grid_template`, and compare client-side instead of doing one query per puzzle.

**[2026-04-05]** Late publish adapters must preserve clue metadata across serialization boundaries — if upload/publish starts reading a new clue field such as `word_type`, every bridge object in the path (`WorkingClue` -> exported puzzle data -> upload payload) must either carry that field or default it explicitly. Otherwise the batch can spend hours reaching “publishable” and then die on a final adapter `AttributeError`.

**[2026-04-07]** Permanent storage cutovers should delete transition-era runtime code, not merely bypass it — once every live row obeys the new invariant, compatibility flags (`is_enabled`, legacy-source branches, resumable backfill state, alias-history side writes) stop being safety mechanisms and start masking contract drift. Collapse the runtime onto the steady-state schema, keep one health audit, and leave migration history only in SQL/docs history.

**[2026-04-07]** Two-model scoring upgrades should keep single-model prompt helpers intact and add consensus one layer up — low-level helpers like `rate_definition()` and `verify_definition_candidates()` are easier to test and reuse when they stay “one request in, one parsed vote out.” Put pair completeness, consensus math, and per-model batching in the phase/runtime layer (`verify`, `theme`, `retitle`) so prompt contracts do not fork and unit tests for raw model I/O stay stable.

**[2026-04-07]** Loaded-model schedulers should prefer model-specific activation hooks over generic “ensure active” wrappers — batch orchestrators need one stable seam that works in production and in tests. If the runtime already exposes `activate_primary()` / `activate_secondary()`, call those first and accept duck-typed model descriptors (`model_id`, `display_name`) from test doubles; falling straight through a generic `ensure_active()` path bypasses mocks, leaks live model-load calls into unit tests, and obscures switch-trace assertions.

**[2026-04-07]** “Shared request helper” is not enough; production LM Studio code also needs a shared dispatch helper — even when every chat call already funnels through one `_chat_completion_create(...)`, scattered `runtime.activate_*()` calls still reintroduce model-switch drift, warm-up hacks, and hidden batching differences. Keep one mandatory dispatch entrypoint above the request helper, and make production phases submit work items to it instead of touching runtime activation directly.

**[2026-04-07]** Strict empty-only model phases need an admission freeze or they starve forever — if a supervisor promises “keep the loaded model until its queue empties” but keeps admitting fresh same-model work after the opposite model already has waiting items, the other side may never run. Once both model queues are non-empty, freeze new admissions until the currently loaded model drains its admitted queue, then switch/reconsider.

**[2026-04-07]** “Model switches observed” does not prove cross-topic parallelism — a supervisor with one global active job can still switch models many times while every other topic is just queued. If the product requirement is “one active task per topic progressing at once,” the orchestration unit must be resumable per-topic job state, not a whole-job call wrapped in a global pending queue.

**[2026-04-07]** Supervisors must not call CLI-era runners as job steps — once `run_all` becomes the unattended production entrypoint, any reused helper that still owns `SystemExit`, signal handlers, shared resume-state files, or internal sleep/poll loops becomes a process-kill hazard. Keep supervisor paths on pure/staged primitives, and treat escaped `SystemExit` as a boundary violation to fail one topic job, not the whole orchestrator.

**[2026-04-07]** Single-model LLM supervisors still need a separate worker lane for long local prep — if Rust/grid fill or other CPU-bound local phases run inline on the same orchestration thread as model scheduling, “multi-topic” slots collapse back into serial progress even after job-state refactors. Keep one serialized LLM lane for all model calls, but move long non-LLM prep attempts onto a conservative background worker and block that lane from doing any final persistence or shared-state writes.

**[2026-04-07]** After lane separation, mixed “persist/apply” helpers become the next hidden scheduler bug — a helper that sometimes writes to Supabase and sometimes quietly does title scoring, canonical arbitration, or referee calls defeats the whole `llm` vs `non_llm` contract. Split those helpers into `prepare_*` (LLM-allowed, returns a pure plan) and `apply_*` (pure writes only), then make the supervisor schedule them as separate stages.

**[2026-04-09]** Pair-eval short-circuit rules must not leak into finalizers as “all votes exist” assumptions — if verification can conclude after one negative model vote, downstream pair finalizers must iterate only over present votes and explicitly mark missing-model state as incomplete when needed. Any direct `votes[model_id]` lookup after short-circuiting is a latent unattended crash.

**[2026-04-09]** Unattended supervisors need deterministic-failure memory, not just per-job retries — retrying a broken `(topic, stable item, stage, error signature)` three times without quarantining it turns a real bug into a day-long silent stall. Keep a run-local failure ledger, emit heartbeat summaries even outside debug mode, back off unhealthy selection targets, and fail fast once the same signature proves deterministic.

**[2026-04-09]** Run-local LLM budget policy should be opt-in state, not a silent global default — task-specific token caps, reasoning downgrades, and truncation-triggered fallback logic are valuable for unattended `run_all`, but they can distort standalone CLIs and unit expectations if applied process-wide by default. Keep the shared chat helper capable of run-local policy injection, but enable the policy explicitly from the unattended entrypoint and reset it around preflight/test boundaries.

**[2026-04-09]** Compatibility wrappers must preserve patch surfaces, not just imports — `from X import *` drops underscore-prefixed helpers and also leaves function globals bound to the original module, so tests patching `generator.foo._helper` or constants on the compatibility module silently stop affecting behavior. For legacy compatibility modules that tests/scripts patch directly, either keep the real implementation there or alias the module identity itself.

**[2026-04-09]** Repo-local `sitecustomize.py` is not reliable when the interpreter already loads a global vendor `sitecustomize` first — bootstrap package paths again inside legacy entry packages (`generator/__init__.py`, `generator/__main__.py`) instead of assuming the repo-local hook will win import precedence.

**[2026-04-09]** After moving Python source under `packages/.../src`, keep one explicit repo-root import bootstrap for the real package name — deleting the legacy namespace is correct, but tests and `python -m rebus_generator ...` still need a deterministic way to resolve `rebus_generator` from repo root. A tiny root package that sets `__path__` to the src package is simpler and less fragile than relying on `sitecustomize.py` or compatibility alias trees.

**[2026-04-10]** Deterministic generate-size dead ends should quarantine the size, not the whole unattended run — when `run_all` already maintains generate-size cooldowns and penalties, repeated generate failures tied to one size choice are selection dead ends, not necessarily code regressions. That includes both Rust phase-1 `fill_grid` unsat failures (`could not generate a valid filled grid`) and stable post-rewrite publishability dead ends (`Could not prepare a publishable ...` with missing definitions / incomplete pair evaluation). Mark the size failed, cool it down, and continue the supervisor; reserve whole-run deterministic quarantine for failures that indicate broken logic rather than one bad size choice.

**[2026-04-10]** Bidirectional compatibility facades across architectural layers are a refactor trap — when `domain/*` re-exports `platform/*` in one place while `platform/*` re-exports `domain/*` in another, the repo keeps old import paths alive at the cost of unclear ownership, broken patch surfaces, and silent runtime drift. Pick one real owner module, make any temporary compat surface explicit and one-way, and add surface-contract tests until the facade is deleted.

**[2026-04-10]** Shared-state wrappers must not become constructor prerequisites during scheduler refactors — if a supervisor moves counters/ledgers behind a new shared object, any helper like `_runtime_load_seconds_total()` that now delegates through that object cannot be called while the object is still being constructed. Seed constructor-time values directly from raw runtime state, then switch helpers to the shared owner after initialization.

**[2026-04-10]** Short-form truncation fallback must treat malformed visible payloads as failures, not successes — for JSON/choice micro-tasks (`title_rate`, `clue_compare`, `clue_tiebreaker`), `finish_reason="length"` with visible but incomplete output is still a bad result. Blank-only retry logic misses the common case where Gemma emits half a JSON object or partial choice text. Keep a purpose-aware “payload unusable” check and trigger the no-thinking retry when truncated content cannot satisfy the parser/contract.

**[2026-04-10]** Equal-rank selector randomization must be seed-owned, not ambient — replacing “pick first on tie” with bare `random.choice(...)` fixes bias but quietly breaks reproducibility in unattended runs and tests. Put one stable RNG helper at the selection layer, derive its seed from run/item/content identity, and pass it only into accidental equal-rank fallbacks; keep deliberate business-rule ties deterministic.

**[2026-04-10]** Template generators must prune with the same slot policy as final validation — if the validator allows a structure (for example edge singletons with orthogonal 2+ coverage) but generator-time placement still uses an older stricter precheck, large-size failures will look like search-budget problems when the real issue is stale pruning logic. Align generator-time guards first; only then trust benchmark results enough to retune attempts/nodes.

**[2026-04-10]** Search benchmark stats must separate search totals from chosen-grid quality metrics — counters like `solver_nodes`, `rejected_*`, and `solved_candidates` should accumulate across attempts, but structural fields like `edge_singletons` or chosen black count must describe the selected candidate only. Summing candidate-local quality metrics across attempts produces impossible benchmark numbers and sends tuning in the wrong direction.

**[2026-04-10]** Base black-target retunes are only half the real start point when dictionary tuning is still active — changing `settings_for_size()` can look correct in code (`15 -> 38`) while live runs still start higher (`42`) because `tune_settings_for_dictionary()` adds a long-word scarcity bonus. Benchmark logs and acceptance checks should distinguish base target from tuned effective target, or future retunes will be misread.

**[2026-04-11]** Static dictionary scarcity should be a sidecar artifact with one builder owner — if phase-1 uses one normalized/deduplicated filter path but scarcity heuristics are recomputed elsewhere (or inline during search), structural knobs, effort scaling, and solver heuristics can silently diverge on what “the dictionary” contains. Build one `words.profile.json` sidecar from the same Rust filter semantics, let size policy own black counts, and let the sidecar drive only effort scaling and solver tie-break heuristics.

**[2026-04-11]** Resettable DB quality fields must be treated as cache/history, never as runtime truth — if a migration or ops reset clears `crossword_clues.verified`, puzzle pass metadata, or canonical quality scores, live publishability and rewrite decisions must still come from fresh in-memory verify/rate results. Whole-puzzle pass rate must not collapse to `0` just because one pair evaluation is incomplete, and canonical ranking must fall back to deterministic neutral ordering when quality evidence is blank instead of treating reset rows as “bad”.

**[2026-04-11]** Ready-unit drain loops need per-cycle dedupe on unchanged unit identity — once a supervisor replans after every unit, a job that forgets to advance state can present the exact same `(job_id, step_id, phase)` forever and hang the run inside one “drain current model” cycle. Keep the global model-drain behavior, but suppress rerunning the same unchanged ready unit within the same scheduler pass; only re-run after state/phase/step identity changes or on a later retry cycle.

**[2026-04-11]** Run-specific session wrappers must either honor the shared session finish contract or avoid shared finalizers entirely — `run_all` used `RunAllRewriteSession` during bounded rewrite rounds, then later called `finish_rewrite_session(...)`, which expects a different session type with `.final_result`. That mismatch surfaced only in late-stage persist prep and retried into deterministic quarantine. If a workflow introduces a wrapper session class, give it an idempotent cached `.finish()` result and keep later stages on that native interface instead of mixing helper APIs across session types.

**[2026-04-18]** Strict short-word leakage needs a local prefix/subform guard, not a global family-root loosening — the shared `clue_uses_same_family()` helper intentionally ignores roots shorter than 4 chars to avoid false positives, so simply removing the short-word bypass in definition validation still lets leaks like `OS` -> `osoasă` through. Keep the general family matcher conservative, and add any stricter 2-3 letter rejection logic at the clue-validation layer where the product policy is explicitly “prefer false rejects over answer leakage.”

**[2026-04-20]** Same-text canonical fallback can still be a real repair when pair evaluation is incomplete — generate-time rescue logic must not treat `fallback.definition == current.definition` as an automatic no-op. If the live clue is blocked only because one model left verify/rate incomplete or unparsable, rehydrating the assessment from a scored canonical representative is enough to unblock strict publishability gates. Scope that hydration narrowly to unresolved generate clues; redefine/no-op flows should not count same-text reassessment as a content change.

**[2026-04-20]** Generate-time canonical rescue needs an unresolved-only policy, not the broad generate fallback policy — `generate_scored_fallback_policy()` also treats missing verify/rate scores as incomplete, which is correct after rewrite but far too broad before rewrite starts. Reusing it in define-finalize would try to canonical-replace every fresh generated clue because none are rated yet. For early rescue, gate only on missing/placeholder definitions, then keep pair-incomplete hydration for the later post-rewrite fallback pass.

**[2026-04-20]** Reasoning transport must separate “thinking enabled” from “reasoning param present” — LM Studio can accept `reasoning_effort="none"` to disable hidden reasoning while also preferring omitted params for thinking-enabled Gemma calls. If code infers “reasoning off” only from missing `reasoning_effort`, omitted-param thinking paths get misclassified and short-form token caps fire incorrectly. Keep abstract reasoning intent separate from backend request params, and carry an explicit `reasoning_enabled` flag through request budgeting and retries.

---

## Archive
<!-- **[YYYY-MM-DD] Archived [YYYY-MM-DD]** title — reason -->

### Python Dependency Management and Execution
**Lesson:** In multi-package or monorepo-like Python projects, migrating to `uv` with a root `pyproject.toml` significantly reduces setup friction. By configuring the build backend (like `hatchling`) to point to the inner `src` directories, you can rely on `uv sync` to install the project in editable mode automatically. This replaces the need for `PYTHONPATH` hacks, manual `venv` orchestration, and custom `sitecustomize.py` scripts that previously injected source paths into `sys.path`. It also unifies dependency management across local dev and GitHub Actions via the `astral-sh/setup-uv` action.
