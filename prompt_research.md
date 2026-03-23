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
1. `system_factor_temperatures`
2. `verify_minimal_procedural`
3. `rewrite_generic_exclusion`
4. `prompt_dedup_cleanup`

Family stop rules:
- stop after 3 consecutive non-keeps
- or 3 total non-keeps since last keep
- or repeated primary fragile-word losses 3+ times
- stop whole campaign after 4 stale families in a row

Selection rules:
- system-factor lane first
- single-file prompt experiments only
- no word-specific examples in this batch
- no multi-file bundles before a clear `keep`

Interpretation rules:
- exact-answer recovery beats creativity
- reject edits that break primary fragile words: `AZ`, `FERMENT`, `MIRE`, `OSTRACA`, `SAN`, `ETAN`
- reject verifier overfitting that fixes one target and harms many unrelated words
