# Iteration Log

> append-only. entry end of every iteration.
> same issue 2+ times? → promote to `LESSONS_LEARNED.md`.

## Entry Format

---

### [YYYY-MM-DD] Brief Description

**Context:** goal / trigger
**Happened:** key actions, decisions
**Outcome:** success / partial / failure
**Insight:** (optional) what to tell next agent
**Promoted:** yes / no

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

<!-- new entries above this line, most recent first -->

### [2026-03-20] Fix generator correctness and objective-alignment bugs on main

**Context:** user asked for task lists plus concrete fixes across multiple passes: correctness, objective alignment, metrics, and tests.
**Happened:** Identified and fixed four core issues: `_best_candidate()` returned after the first solved grid; LM Studio model switching unloaded by model key instead of loaded instance id; clean `defs.md` export kept score residue; clue selection and rewrite gating underweighted exact verification. Added richer word-difficulty aggregation fields (`wrong_guess`, `failure_kind`, blocker counts, rebus/guessability averages, rarity-override counts, word type). Added focused tests for model switching, selection ranking, best-candidate search, clean export, and richer metrics. Wrote a pass-based task list under `build/experiment_reports/20260320_generator_task_list.md`.
**Outcome:** success
**Insight:** selector/rule mismatches between assessment and production create false-positive prompt wins; correctness fixes and objective alignment should land before the next baseline
**Promoted:** yes — see LESSONS_LEARNED entries on selection alignment and LM Studio unload instance ids
