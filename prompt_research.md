# Prompt Research Program

Goal: improve exact multistep benchmark performance under the reset regime, not by chasing old pre-reset highs.

Rules:
- mutate prompt files only during autonomous prompt trials
- benchmark dataset fixed during one campaign
- restore incumbent prompt snapshot after every non-keep
- compare candidate vs incumbent with replicated runs, not one noisy run
- safe-stop on stale families; no auto-restart

Statuses:
- `keep`: proven improvement; adopt immediately
- `uncertain`: reusable signal; possible future bundle or rewrite
- `discard`: bad idea in current form

Current experiment families:
1. `verify_romanian_only`
2. `verify_resolution_compaction`
3. `verify_targeted_examples`
4. `verify_user_exactness`
5. `rate_exact_answer_calibration`
6. `rate_rare_sense_calibration`
7. `definition_positive_romanian_sense`
8. `definition_vague_neighbor_counterexamples`

Family stop rules:
- stop after 3 consecutive non-keeps
- or 3 total non-keeps since last keep
- or repeated primary fragile-word losses 3+ times
- stop whole campaign after 4 stale families in a row

Selection rules:
- verify first, then rate, then definition
- pause rewrite-only batches for now
- prefer short, positive Romanian-first phrasing over negative bans
- use replicated comparison summaries as decision artifacts
- historical `results1.tsv` ... `results8.tsv` are evidence only, not current-regime targets
- start from the confirmed `v4exp001` prompt as incumbent

Interpretation rules:
- exact-answer recovery beats creativity
- pass-rate mean and tier-balanced pass-rate mean drive keep/discard
- composite stays visible, but no longer decides alone
- reject edits that break primary fragile words: `AZ`, `FERMENT`, `MIRE`, `OSTRACA`, `SAN`, `ETAN`
- reject verifier overfitting that fixes one target and harms many unrelated words
- refresh DEX in assessment from DexProvider cache/Supabase before falling back to dataset snapshots

Manual runbook:
- baseline only:
  - `.venv/bin/python -u -m generator.assessment.run_assessment --description "baseline_results_20260329_v4exp001" --json-out logs/baseline_results_20260329_v4exp001.json`
- one-off `v6` probe; fresh ad-hoc state/log dir:
  - `.venv/bin/python scripts/run_experiments.py --experiment-set v6 --start-from 1 --end-at 1 --log-path build/manual_v6/experiment_log.json --assessment-logs-dir build/manual_v6/assessment_logs --description-prefix manual_v6/ --comparison-runs 3 --stream-assessment-output`
- `v6` probes:
  - `1-4` = verify lane
  - `5-6` = rate lane
  - `7-8` = definition lane
- monitor:
  - `tail -f build/manual_v6/assessment_logs/v6exp001.candidate.run1.log`
  - `tail -n 5 generator/assessment/results.tsv`
  - `python3 -m json.tool build/manual_v6/assessment_logs/v6exp001.comparison.json | less`
