# Prompt Research Program

Goal: improve exact multistep benchmark performance through prompt edits only.

Rules:
- mutate only files under `generator/prompts/`
- never mutate pipeline code during autonomous runs
- benchmark dataset fixed during one campaign
- restore incumbent prompt snapshot after every non-keep
- safe-stop on stale families; no auto-restart

Statuses:
- `keep`: proven improvement; adopt immediately
- `uncertain`: reusable signal; possible future bundle or rewrite
- `discard`: bad idea in current form

Current family priority:
1. `definition_examples`
2. `definition_rewrite_bundles`
3. `rate_exactness`
4. `rewrite_anti_distractor`
5. `verify_examples_short`
6. `verify_examples_rare`
7. `verify_bundles`
8. `definition_rate_bundles`
9. `confirm_bundles`
10. `cleanup`

Family stop rules:
- stop after 4 consecutive non-keeps
- or 6 total non-keeps since last keep
- or repeated collateral losers 3+ times
- stop whole campaign after 3 stale families in a row

Selection rules:
- single-file experiments before bundles
- bundle families unlock only after related families show keep or research-signal uncertain
- skip stale families

Interpretation rules:
- prioritize low/medium-word gains
- reject edits that break protected controls
- reject verifier overfitting that fixes one target and harms many unrelated words
