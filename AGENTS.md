# AGENTS.md

Style: telegraph. Noun-phrases OK. No grammar. Min tokens.

> Bootstrap context only
- Discoverable from codebase → omit.
> Corrections/patterns → `LESSONS_LEARNED.md`.
> Development:
- Correctness first
- Smallest good change
- Preserve behavior/interfaces/invariants
- Simple, explicit code
- KISS; YAGNI; DRY
- Temp duplication OK during migration
- High cohesion; low coupling
- Follow repo patterns
- Refactor if patch adds complexity
- Broad changes: Coherent end-state; staged; verifiable
- No unrelated churn
- Leave code better
> Validation:
- Fast proof
- Targeted tests first
- Typecheck/build/lint
- Smoke tests if useful
- Update tests on intentional change
> Ambiguity:
- Code unclear -> Explain; ask; no assume
- Choose reversible path; state assumption

## Constraints

- **Never blocklist Romanian words**: Use quality scoring, thresholds, penalties. Blocklist = risk.
- **Dev server**: LM Studio local `http://localhost:1234`.
- **Two-model workflow**: gemma-4 + eurollm-22b default. Alternate rewrites. Load via LM Studio REST API. Config: `packages/rebus-generator/src/rebus_generator/platform/llm/models.py`.

## Legacy & Deprecated

(Empty)

## Learning System

Session:
1. Start: Read `LESSONS_LEARNED.md`
2. During: Note surprises
3. End: Append `ITERATION_LOG.md`
4. Reusable insight? → Add `LESSONS_LEARNED.md`
5. Repeat issue? → Promote to `LESSONS_LEARNED.md`
6. Surprise? → Flag to dev

| File | Purpose | Write When |
|------|---------|------------|
| `LESSONS_LEARNED.md` | Wisdom + corrections | Reusable insight |
| `ITERATION_LOG.md` | Session journal | Every iteration |

Rules: No delete from `ITERATION_LOG.md`. Obsolete lessons → Archive. Date-stamp YYYY-MM-DD.

### Periodic Maintenance
Audit configs via `SETUP_AI_AGENT_CONFIG.md`.

## Development Tools

Use `Makefile` for task automation:
- `make lint`: Run ruff check and format verification.
- `make test`: Run pytest suite.
- `make format`: Auto-format with ruff.

| Agent | File | When |
|-------|------|------|
| Romanian Crossword Expert | `.claude/agents/romanian-crossword-expert.md` | Romanian linguistics, morphology, definition quality |
| Architect | `.claude/agents/architect.md` | System design, pipeline, ADRs |
| Planner | `.claude/agents/planner.md` | Multi-step plans |
| UX Expert | `.claude/agents/ux-expert.md` | UI, interaction, a11y |
| Agent Creator | `.claude/agents/agent-creator.md` | New agent needed |
| Prompt Engineer Expert | `.claude/agents/prompt-engineer-expert.md` | Prompt optimization |
