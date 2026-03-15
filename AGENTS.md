# AGENTS.md — Generator Rebus

## Non-Discoverable Constraints

- **Never blocklist Romanian words** — use softer alternatives (quality scoring, definability thresholds, rarity penalties). Blocklisting words is a slippery slope that makes the generator worse over time.
- **Dev server**: LM Studio assumed running locally at `http://localhost:1234`.
- **Two-model workflow**: gpt-oss-20b and eurollm-22b alternate across rewrite rounds. Models are loaded/unloaded via LM Studio REST API. Do not assume a single model.

## Files for Context

- [GENERATOR_ARCH.md](GENERATOR_ARCH.md) — **start here**: full pipeline pseudocode, randomness map, temperature table, quality gate thresholds, and rationale for non-obvious design choices. Read before proposing changes to the generation pipeline. Keep updated when pipeline logic changes.
- [LESSONS_LEARNED.md](LESSONS_LEARNED.md) — known pitfalls, failure modes, and their solutions
- [ITERATION_LOG.md](ITERATION_LOG.md) — recent changes, decisions, and outcomes

## Sub-Agents

| Agent | Scope | When to Use |
|-------|-------|-------------|
| `romanian-crossword-expert` | Romanian linguistics, morphology, definition quality | Validating word families, prefix/suffix analysis, crossword conventions |
| `architect` | System design, pipeline architecture | Structural changes, new pipeline phases, performance decisions |
| `planner` | Implementation planning | Complex features, multi-file changes, refactoring |
| `agent-creator` | Creating/updating agent definitions | New agents, rewriting agent files to follow structure |
