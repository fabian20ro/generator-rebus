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

## Code Patterns & Pitfalls

**[2026-03-14]** Short words (OU, AT, OF) need special handling — for 2-letter words, any definition almost inevitably contains the answer. English homograph hints inject correct Romanian meaning. Preset definitions (AT, OF) bypass LLM entirely. `_definition_describes_english_meaning()` guard rejects English-meaning definitions.

**[2026-03-14]** Family check needs prefix stripping — `clue_uses_same_family` only stripped suffixes. Prefixed words (NEINCEPUT→ÎNCEPUT) weren't caught. Added `ROMANIAN_PREFIXES` list, `forbidden_definition_stems()`, and `_family_exclusion_note()` in prompt builders.

**[2026-03-20]** Keep production selection aligned with assessment selection — if the benchmark ranks verified/exact clues first but production still prefers semantic+rebus totals, prompt experiments optimize the wrong target. Change selector and rewrite gates together, then retest.

**[2026-03-20]** `locked` state must follow exact verification, not only score thresholds — a 9/8 clue that still guesses wrong can get skipped forever in rewrite rounds if lock logic only checks semantic/rebus. Keep lock semantics aligned with `_needs_rewrite()`.

## Testing & Quality
**[2026-03-18]** `rate_puzzle()` tests must mock `DexProvider.for_puzzle()` — otherwise `tests/test_verify.py` can hang or become environment-dependent during DEX prefetch. Unit tests for verify/rate flow should stub DEX access explicitly.

## Performance & Infrastructure
<!-- **[YYYY-MM-DD]** title — explanation -->

## Dependencies & External Services
<!-- **[YYYY-MM-DD]** title — explanation -->

**[2026-03-20]** Top-k verifier changes need pipeline-wide semantics, not just a prompt tweak — if verify starts returning 2-3 candidates, pass/fail, exported notes, metrics, and assessment all need to treat “any candidate matches” as success. Otherwise near-miss data is lost and benchmark semantics drift from production.

**[2026-03-20]** LM Studio unload calls must use loaded `instance_id`, not model key — `/api/v1/models` exposes loaded instances separately from model keys, and switching by key can silently leave the old model loaded. In two-model workflows, always resolve the active instance id before unloading.

**[2026-03-20]** “Publishable” needs an exact-solve floor, not only “no blockers” — otherwise puzzles with weak multistep pass rates can still ship just because every clue cleared loose score thresholds. Gate publication on both blocker-free state and a minimum verification pass rate.

**[2026-03-20]** Invalid JSON from the rating model needs a stricter retry prompt, not a blind resend — LM Studio-compatible local models can drift out of schema even with low temperature. On parse failure, re-ask explicitly for one JSON object only; otherwise reruns waste one of the limited attempts.

**[2026-03-20]** First-pass and final-pass metrics must be stored separately — if the rewrite loop only returns final verified counts, any reported `definition_first_pass_rate` is fake and churn analysis becomes misleading. Track `first_passed` and `final_passed` explicitly in prepared puzzles and metrics.

## Process & Workflow
**[2026-03-18]** Prompt experiment runs must roll back assessment artifacts on discard — `run_assessment.py` always appends to the assessment results TSV, so an outer hill-climber cannot trust "last row = current best" unless it snapshots and restores the TSV for discarded or interrupted experiments. Experiment logs also need per-campaign isolation or reset support, otherwise reruns silently skip prior experiment names.

**[2026-03-18]** Interrupted prompt campaigns can leave prompt files mid-experiment — if a run stops due to power loss or crash, compare `generator/prompts/` against the campaign backup dir before trusting the working tree. The current prompt files may contain the next experiment's unreviewed edit even when no result was recorded.

**[2026-03-18]** One log per experiment beats one monolithic campaign log — full multistep assessments are too verbose to share a single append-only file. Use a campaign JSON/TSV for summaries and a separate `expNNN.log` file for each assessment run.

**[2026-03-20]** Live git experiment commits are not enough to reconstruct winning prompt state — when the runner commits prompt edits before assessment and later tries to commit results, ignored log paths or interrupted runs can desynchronize git history, prompt backups, and the results TSV. Treat the results TSV as score history, not as authoritative prompt-state history; restore or diff prompt backups explicitly before starting the next campaign.

---

## Archive
<!-- **[YYYY-MM-DD] Archived [YYYY-MM-DD]** title — reason -->
