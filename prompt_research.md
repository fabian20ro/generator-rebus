# Prompt Research Program

Goal: Improve exact multistep benchmark performance under reset regime. No chase pre-reset highs.

Rules:
- Mutate prompt files during autonomous trials only.
- Fixed benchmark dataset per campaign.
- Restore incumbent prompt snapshot after non-keep.
- Compare candidate vs incumbent via replicated runs.
- Stop on stale families; no auto-restart.

Statuses:
- `keep`: Proven improvement; adopt.
- `uncertain`: Reusable signal; possible bundle/rewrite.
- `discard`: Bad idea.

Families:
1-4: Verify (only, resolution, examples, exactness).
5-6: Rate (calibration).
7-8: Definition (positive sense, neighbor counterexamples).

Stop Rules:
- 3 consecutive non-keeps.
- 3 total non-keeps since last keep.
- Fragile-word losses 3+ times.
- Stop campaign after 4 stale families in row.

Selection:
- Verify -> Rate -> Definition.
- Pause rewrite batches.
- Positive Romanian phrasing.
- Incumbent: `v4exp001`.

Interpretation:
- Exact-answer recovery > creativity.
- Keep/discard driven by pass-rate mean + tier-balanced mean.
- Reject edits breaking fragile words: `AZ`, `FERMENT`, `MIRE`, `OSTRACA`, `SAN`, `ETAN`.
- Reject verifier overfitting.
- Refresh DEX from `DexProvider`/Supabase.

Runbook:
- Baseline: `uv run python -u -m rebus_generator.cli.assessment ...`
- Probe: `uv run python tools/scripts/run_prompt_campaign.py --experiment-set v6 ...`
- `v6` lanes: `1-4` verify, `5-6` rate, `7-8` definition.
- Monitor: `candidate.run1.log`, `results.tsv`, `comparison.json`.
