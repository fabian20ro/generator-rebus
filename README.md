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
