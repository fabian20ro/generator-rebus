# AGENTS.md

work style: telegraph; noun-phrases ok; drop grammar; min tokens.

> bootstrap context only. discoverable from codebase → don't put here.
> corrections + patterns → LESSONS_LEARNED.md.

## Constraints

<!-- non-obvious, needed BEFORE exploring. keep minimal. -->
- **never blocklist Romanian words** — softer alternatives (quality scoring, definability thresholds, rarity penalties). blocklisting = slippery slope.
- **dev server**: LM Studio running locally `http://localhost:1234`.
- **two-model workflow**: gpt-oss-20b + eurollm-22b alternate rewrite rounds. models loaded/unloaded via LM Studio REST API. never assume single model.

## Legacy & Deprecated

<!-- codebase parts that actively mislead. -->

## Learning System

Every session:
1. start: read `LESSONS_LEARNED.md`
2. during: note surprises
3. end: append `ITERATION_LOG.md`
4. reusable insight? → also add `LESSONS_LEARNED.md`
5. same issue 2+ times in log? → promote to `LESSONS_LEARNED.md`
6. surprise? → flag to developer (they decide: fix codebase / update LESSONS_LEARNED / adjust this file)

| File | Purpose | Write When |
|------|---------|------------|
| `LESSONS_LEARNED.md` | curated wisdom + corrections | reusable insight gained |
| `ITERATION_LOG.md` | raw session journal, append-only | every iteration |

Rules: never delete from ITERATION_LOG. Obsolete lessons → Archive in LESSONS_LEARNED. Date-stamp YYYY-MM-DD. When in doubt: log it.

### Periodic Maintenance
Config files audited periodically via `SETUP_AI_AGENT_CONFIG.md`.
See "Periodic Maintenance Protocol" section.

## Sub-Agents

`.claude/agents/`. Invoke proactively.

| Agent | File | When |
|-------|------|------|
| Romanian Crossword Expert | `.claude/agents/romanian-crossword-expert.md` | Romanian linguistics, morphology, definition quality |
| Architect | `.claude/agents/architect.md` | system design, pipeline architecture, ADRs |
| Planner | `.claude/agents/planner.md` | complex multi-step — plan before code |
| UX Expert | `.claude/agents/ux-expert.md` | UI, interaction, a11y (frontend) |
| Agent Creator | `.claude/agents/agent-creator.md` | new agent needed for recurring domain |
| Prompt Engineer Expert | `.claude/agents/prompt-engineer-expert.md` | prompt optimization for definition pipeline (assessment-driven) |
