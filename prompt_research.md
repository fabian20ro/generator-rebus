# Prompt Research Program

Goal: improve exact multistep benchmark performance through prompt edits and a small set of system-factor trials.

Rules:
- mutate prompt files only during autonomous prompt trials
- system-factor trials may change assessment temperatures only
- benchmark dataset fixed during one campaign
- restore incumbent prompt snapshot after every non-keep
- safe-stop on stale families; no auto-restart

Statuses:
- `keep`: proven improvement; adopt immediately
- `uncertain`: reusable signal; possible future bundle or rewrite
- `discard`: bad idea in current form

Current experiment families:
1. `header_signal_isolation`
2. `header_signal_blends`
3. `precision_support`

Family stop rules:
- stop after 3 consecutive non-keeps
- or 3 total non-keeps since last keep
- or repeated primary fragile-word losses 3+ times
- stop whole campaign after 4 stale families in a row

Selection rules:
- rewrite lane only
- single-file prompt experiments only
- no verify/rate/temperature changes in this batch
- no multi-file bundles before a clear `keep`
- avoid negative bans that repeat the unwanted token; prefer positive Romanian-sense / referent-first phrasing
- start from the confirmed `v4exp001` prompt as incumbent

Interpretation rules:
- exact-answer recovery beats creativity
- reject edits that break primary fragile words: `AZ`, `FERMENT`, `MIRE`, `OSTRACA`, `SAN`, `ETAN`
- reject verifier overfitting that fixes one target and harms many unrelated words

Manual runbook:
- baseline only:
  - `.venv/bin/python -u -m generator.assessment.run_assessment --description "baseline_results_20260329_v4exp001" --json-out logs/baseline_results_20260329_v4exp001.json`
- one-off `v5` probe; fresh ad-hoc state/log dir:
  - `.venv/bin/python scripts/run_experiments.py --experiment-set v5 --start-from 1 --end-at 1 --log-path build/manual_v5/experiment_log.json --assessment-logs-dir build/manual_v5/assessment_logs --description-prefix manual_v5/ --stream-assessment-output`
- `v5` probes:
  - `1-3` = isolate header/context/periphrasis signal from `exp004`
  - `4-5` = blend best `exp004` framing pieces onto `exp001`
  - `6-8` = add form-sensitive and rare-sense precision support
- monitor:
  - `tail -f build/manual_v5/assessment_logs/v5exp001.log`
  - `tail -n 5 generator/assessment/results.tsv`
  - `python3 -m json.tool build/manual_v5/assessment_logs/v5exp001.json | less`
