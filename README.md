# generator-rebus

Romanian rebus generator. Pipeline CLI: build puzzles from Scrabble dictionary, LLM definitions, publish to web.

## Current map

- `workflows/generate/service.py`: Main gen workflow. Candidate grids, definition/rewrite/title, publish.
- `domain/`: Puzzle/clue state, scoring, selection, rules, logic.
- `platform/llm/`: LM Studio client, registry, prompt helpers.
- `evaluation/`: Assessment, datasets, policy, reports, tools.
- `engines/crossword-engine/`: Rust fill engine.
- `apps/frontend/`: Static client. Reads worker API.
- `apps/worker/`: Cloudflare Worker. Supabase proxy.
- `tests/`: Prompt, selection, quality, title, verify tests.
- `run_all.sh`: Production entrypoint. `generate`, `redefine`, `retitle`, `simplify`.

```
run_all / cli -> workflows -> platform -> engines/Supabase/LM Studio/apps
```

## Publication quality

New puzzles require a consensus pass rate of at least `50%` and a minimum
rebus score of `5/10`. Override these defaults with
`PUBLICATION_MIN_PASS_RATE` and `PUBLICATION_MIN_REBUS_SCORE`.

Audit the public catalog without mutations:

```sh
uv run python -m rebus_generator.workflows.generate.catalog_audit \
  --api-base https://generator-rebus.fabian20ro.workers.dev \
  --output build/catalog-quality.json
```

Use `--input puzzles.json` instead of `--api-base` for a saved API response.

Build the deterministic recovery queue and require at least three qualifying
puzzles for every supported size:

```sh
uv run python -m rebus_generator.workflows.generate.catalog_recovery \
  --api-base https://generator-rebus.fabian20ro.workers.dev \
  --output build/catalog-recovery.json \
  --require-ready
```

Exit code `2` means rollout remains blocked. Repair the reported `repair_queue`
sizes first; rerun until `rollout_ready` is `true`. The configured Gemma 4 and
EuroLLM model IDs must both be installed in LM Studio; `run_all` preflight now
fails before unloading any active model when either is absent.

After the recovery plan is ready, enable the Worker filter with
`CATALOG_QUALITY_FILTER=true`. Optional Worker variables
`CATALOG_MIN_PASS_RATE` and `CATALOG_MIN_REBUS_SCORE` default to `0.5` and `5`.
The filter applies to list, detail, and solution routes. Keep it disabled during
catalog recovery to avoid removing under-covered sizes from the player.
