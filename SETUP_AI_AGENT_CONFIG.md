# AI Agent Config Setup Guide

> Roles: (1) setup, (2) maintenance (audit + clean).
> Use: new project, onboarding, hygiene (weekly/monthly/yearly).

---

## Research Context

- **[Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988)**: LLM context files (`/init`): -3% success, +20% cost. Developer-provided: +4% marginal. Context files = broader exploration.
- **[SkillsBench](https://arxiv.org/abs/2602.12670)**: Focused skills (2-3 modules) > comprehensive docs. Self-generated: zero benefit. Small models + skills ~= large models without.
- **[Yu et al. 2026](https://arxiv.org/abs/2602.12670)**: Multi-agent memory = computer architecture. Three-layer hierarchy. Cache sharing + access control = protocol gaps. Memory consistency = hardest challenge.

**Core principle:** help model, avoid distraction. Codebase info -> skip in config.

---

## File Synchronization Model

Four types. Distinct roles. Overlap = bug.

```text
AGENT STARTS TASK
  1. Read AGENTS.md (bootstrap - smallest context)
     -> constraints, legacy traps, file refs
     -> LESSONS_LEARNED.md pointer + sub-agents

  2. Read LESSONS_LEARNED.md (curated wisdom)
     -> corrections + patterns from past sessions
     -> repeated mistakes live here, not AGENTS.md

  3. Complex task? -> delegate sub-agent
     -> .claude/agents/architect.md, planner.md, etc.
     -> focused procedural knowledge, load on demand

  4. Work

  5. End iteration:
     -> append ITERATION_LOG.md
     -> reusable insight? -> LESSONS_LEARNED.md
     -> surprise? -> developer flag

  Developer choice:
     -> fix codebase (preferred)
     -> add LESSONS_LEARNED (if no codebase fix)
     -> add AGENTS.md (non-discoverable constraints)
     -> new sub-agent (agent-creator)

  PERIODIC MAINTENANCE (weekly/monthly/yearly/new model):
     -> hand doc to agent as task
     -> agent audits all files, removes stale, promotes patterns
     -> files lean, never fat
```

### Boundary rules

| Question | -> File |
|----------|---------|
| Discoverable from codebase? | **Nowhere** |
| Constraint needed BEFORE exploring? | **AGENTS.md** |
| Correction for repeated mistake? | **LESSONS_LEARNED.md** |
| Raw observation, single session? | **ITERATION_LOG.md** |
| Focused procedural knowledge, recurring domain? | **Sub-agent** `.claude/agents/` |
| Codebase keeps confusing agents? | **Fix codebase** -> `LESSONS_LEARNED.md` (fallback) |

### Memory hierarchy (Yu et al. 2026)

Map file system to three-layer architecture.

```text
LAYER             HARDWARE ANALOGY       OUR FILES
--------------------------------------------------------
Boot / ROM        BIOS, firmware         AGENTS.md
                  Startup read.          Read once. Smallest. Static.

Cache             L1/L2 cache            Current session context
                  Fast, limited.         Conversation, tools, files.
                  Volatile.              NOT persisted.

Shared memory     Main RAM               LESSONS_LEARNED.md
                  All read.              Read at start. Persist end of
                  Consistency needed.    iteration.

Write-ahead log   Transaction log        ITERATION_LOG.md
                  Append-only.           Source of truth. Never deleted.
                  Recovery.

Local memory      Per-core scratchpad    Sub-agents (.claude/agents/)
                  Proc-specific.         Domain-specific. Load on demand.

Disk / storage    SSD, persistent        Codebase
                                         Explore on demand.
                                         NO config duplication.
```

**Insight:** lag = data in wrong layer. `AGENTS.md` (ROM) correction instead of `LESSONS_LEARNED.md` (RAM) = firmware var instead of memory.

### Access control

| File | Main agent | Sub-agents | Maintenance agent | Developer |
|------|------------|------------|-------------------|-----------|
| `AGENTS.md` | **Read** | **Read** | **Read+Write** (audit) | **Write** (authority) |
| `LESSONS_LEARNED.md` | **Read+Write** | **Read** | **Read+Write** (audit) | **Write** (authority) |
| `ITERATION_LOG.md` | **Append** | **Append** | **Read** | **Read** |
| Sub-agent files | **Read** | **Read** (own) | **Read+Write** (audit) | **Write** (authority) |

Rules:
- Sub-agents report to main agent, never edit `AGENTS.md`/`LESSONS_LEARNED.md`.
- Main agent or developer only for `LESSONS_LEARNED.md` updates.
- `ITERATION_LOG.md` = sub-agents direct append (no conflict).
- Maintenance agent = automated `AGENTS.md` audit access.

### Consistency model (concurrent sessions)

**`ITERATION_LOG.md`**: no conflicts. Append-only. Git merge clean.
**`LESSONS_LEARNED.md`**: last-writer-wins + review. Conflicts -> git merge -> flag.
**`AGENTS.md`**: immutable between maintenance.
**Sub-agents**: immutable during sessions. Change via maintenance or `agent-creator`.

**Rule:** merge conflicts = wrong architecture/too many writers.

### Promotion flow

```text
Observation (single) -> ITERATION_LOG.md
Issue 2+ times -> promote to LESSONS_LEARNED.md
Obsolete -> Archive in LESSONS_LEARNED
Recurring domain -> agent-creator -> sub-agent
New model -> delete AGENTS.md, test, re-add breaks
          -> archive handled LESSONS_LEARNED
Maintenance -> doc to agent, audit
```

---

## File Structure

```text
project-root/
|- AGENTS.md                 # bootstrap (minimal, non-discoverable)
|- CLAUDE.md                 # -> AGENTS.md
|- GEMINI.md                 # -> AGENTS.md
|- LESSONS_LEARNED.md        # curated wisdom
|- ITERATION_LOG.md          # session journal
|- SETUP_AI_AGENT_CONFIG.md  # setup + maintenance protocol
`- .claude/
   `- agents/
      |- architect.md        # design, ADRs
      |- planner.md          # multi-step plans
      |- agent-creator.md    # create agents
      `- ux-expert.md        # UI/UX
```

---

## Step 1: `CLAUDE.md` and `GEMINI.md`

Content:

```markdown
Read AGENTS.md asap
```

Minimal redirect.

---

## Step 2: `AGENTS.md`

Context window priority. Every token costs attention. Non-discoverable info only.

### Exclude

- architecture overviews
- dependencies
- scripts/commands
- folder structure
- enforced lint rules
- generic best practices
- `/init` content
- repeated mistake corrections -> `LESSONS_LEARNED.md`

### Include

- non-obvious tooling constraints ("pnpm workspace quirks")
- environment assumptions ("dev server running")
- legacy traps ("legacy /api/ deprecated")
- pointers: `LESSONS_LEARNED.md`, `ITERATION_LOG.md`, sub-agents

### Mandatory Preamble

`AGENTS.md` MUST start with:

```markdown
# AGENTS.md

work style: telegraph; noun-phrases ok; drop grammar; min tokens.

> bootstrap context only
- discoverable from codebase → don't put here.
> corrections + patterns → LESSONS_LEARNED.md.
> development:
- correctness first
- smallest good change
- preserve behavior / interfaces / invariants unless task says otherwise
- simple, explicit code
- KISS
- YAGNI
- DRY; rule of three; temp duplication ok during migration
- high cohesion; low coupling
- follow repo patterns unless intentionally replacing with better consistent one
- refactor when patch would raise future complexity
- for broad changes: optimize for coherent end-state; stage changes; each step verifiable
- no unrelated churn
- leave code better
> validation:
- fastest relevant proof
- targeted tests first
- typecheck / build / lint as needed
- smoke tests for affected flows when useful
- update tests when behavior intentionally changes
> ambiguity:
- cannot decide from code -> explain; ask; no assume
- otherwise choose most reversible reasonable path; state assumption
```

### Maintenance Philosophy

`AGENTS.md` shrink priority:
- monthly audit: delete stale
- new model: delete, test, re-add minimum
- NO `/init`
- fix codebase > config entry

---

## Step 3: Sub-Agents `.claude/agents/`

SkillsBench: 2-3 module skills -> +16.2pp pass.

### `architect.md`

````markdown
# Architect
design, scalability, decisions.

## Activation
Proactive:
- 3+ module touch
- large refactor / data flow change
- tech selection
- ADR updates

## Role
Senior architect. Holistic thinking. Simplicity, changeability, boundaries, data flow.

## Output Format
### Design Decision
```text
## Decision: [Title]
Context: [problem]
Options: A [tradeoffs] / B [tradeoffs]
Decision: [chosen]
Why: [reasoning]
Consequences: [implications]
```

### System Change
```text
## Change: [Title]
Current: [status quo]
Proposed: [new state]
Migration: [steps]
Risk: [pitfalls]
Affected: [modules]
```

## Principles
- simplest solution. complexity requires justification.
- decisions as ADRs.
- change A -> B = design smell.
- composition > inheritance. functions > classes.
````

### `planner.md`

````markdown
# Planner
Complex feature planning.

## Activation
Proactive:
- 3+ file span
- specific step order
- retry failed attempt
- new feature request

## Role
Break down work -> verifiable steps. Plan only, no code.

## Output Format
```text
# Plan: [Feature]

## Overview
[short what/why]

## Prerequisites
- [ ] [pre-conditions]

## Phases

### Phase 1: [Name] (est: N files)
1. **[Step]** - `path/to/file`
   - action: [specific]
   - verify: [how]
   - depends: X

### Phase 2: [Name]
...

## Verify
- [ ] end-to-end
- [ ] type/lint
- [ ] tests

## Rollback
[undo]
```

## Principles
- step verification mandatory.
- 1-3 files per phase.
- front-load risk.
- address previous failures.
````

### `ux-expert.md`

````markdown
# UX Expert
UI, components, interactions.

## Activation
Proactive:
- new UI/pages
- interaction flow
- a11y decisions
- UI patterns
- responsive layouts

## Role
Senior UX engineer. Design <-> implementation bridge. Human focus.

## Output Format
### Component
```text
## Component: [Name]
Goal: [achievement]
Interaction: [method]
States: empty/loading/populated/error/disabled
A11y: keyboard/screen reader/ARIA
Responsive: mobile/tablet/desktop
Edge cases: long text/empty/many
```

### Flow
```text
## Flow: [Name]
Entry: [start]
Happy path: [steps]
Error paths: [recovery]
Feedback: [step visual]
```

## Principles
- keyboard a11y mandatory.
- loading/error design first.
- empty state = opportunity.
- animation: respect user settings.
- mobile touch targets 44px min.
````

### `agent-creator.md`

````markdown
# Agent Creator
Meta-agent. Design + create sub-agents.

## Activation
- recurring expertise domain
- dev request
- agent scope too broad -> split

## Reference Archetypes
Structure via `.claude/agents/`.
Patterns: https://github.com/affaan-m/everything-claude-code/tree/main/agents

| Archetype | For | Source |
|-----------|-----|--------|
| architect | design, ADRs | local |
| planner | plans | local |
| ux-expert | frontend | local |
| code-reviewer | quality | remote |
| tdd-guide | tests | remote |
| security-reviewer | security | remote |

## Design Rules
1. Focus: 2-3 modules max.
2. Structure: Name, Description, Activation, Role, Output, Principles.
3. Anti-patterns: duplicated info, overlapping agents, scope > 100 lines.
4. Registration: update `AGENTS.md` table.

## Output
1. `.md` content.
2. Path: `.claude/agents/[name].md`.
3. `AGENTS.md` table update.

## Validation
[v] 3+ triggers
[v] output template
[v] 3-5 principles
[v] no discoverable info
[v] no overlap
[v] scope <= 2-3 modules
[v] <= 100 lines
[v] AGENTS.md updated
````

### Handoff protocol

Sub-agent -> main agent. Cache transfer. Self-contained results.

```text
## Handoff: [Sub] -> [Main]
Task: [request]
Result: [ADR/plan/spec]
Artifacts: [paths]
Open questions: [issues]
Next step: [who/what]
```

Rules:
- main agent orchestrates.
- sub-agent artifacts pass via main agent.
- persist decisions to disk.
- log smells to `ITERATION_LOG.md`.

---

## Step 4: `LESSONS_LEARNED.md`

Curated wisdom. Repeated corrections. Read every task. Update every iteration.

```markdown
# Lessons Learned
> AI managed. Validated insights. Read at start. Update at end.

## Usage
- start: avoid mistakes.
- end: new insight? -> add.
- promotion: 2+ times in log -> promote.
- pruning: Archive obsolete (reason + date). No deletion.

---
## Categories
- Architecture/Design
- Code Patterns/Pitfalls
- Testing/Quality
- Performance/Infra
- Dependencies
- Process/Workflow

---
## Archive
```

---

## Step 5: `ITERATION_LOG.md`

Append-only journal. Source for `LESSONS_LEARNED.md`.

```markdown
# Iteration Log
> 2+ occurrences -> promote to LESSONS_LEARNED.md.

## Format
---
### [YYYY-MM-DD] Description
**Context:** goal
**Happened:** actions
**Outcome:** result
**Insight:** next agent tips
**Promoted:** yes/no
---
```

---

## Step 6: Git

```bash
git add AGENTS.md CLAUDE.md GEMINI.md LESSONS_LEARNED.md ITERATION_LOG.md SETUP_AI_AGENT_CONFIG.md .claude/agents/ .github/pull_request_template.md
git commit -m "chore: add AI agent config + memory system"
```

PR Template:

```markdown
## AI Agent Checklist
- [ ] ITERATION_LOG.md appended
- [ ] LESSONS_LEARNED.md promoted
- [ ] AGENTS.md audited (shrink, not grow)
```

---

## Verification

[v] Redirects minimal
[v] AGENTS.md minimal, telegraph preamble
[v] Sub-agents exist
[v] LESSONS_LEARNED/ITERATION_LOG templates correct
[v] Zero duplication/overlap
[v] One layer per info type

---

## Decision Flowchart

Mistake? -> fix codebase? -> log -> LESSONS_LEARNED
Pre-exploration info? -> discoverable? -> nowhere / AGENTS.md
Complex? -> planner
Architecture? -> architect -> ADR
Frontend? -> ux-expert
New domain? -> agent-creator
New model? -> delete/audit
Growing AGENTS.md? -> move to LESSONS_LEARNED / delete discoverable
Maintenance? -> run protocol

---

## Periodic Maintenance Protocol

Hand doc to agent: *"Run maintenance protocol."*

### Frequency

| Cadence | Trigger |
|---------|---------|
| weekly | active project |
| monthly | default |
| model release | major cleanup |

### Phase 1: Audit `AGENTS.md`

Goal: minimal.
- discoverable? -> remove
- stale? -> remove
- corrections? -> move to LESSONS_LEARNED
- preamble present?

### Phase 2: Audit `LESSONS_LEARNED.md`

- accurate? -> archive if obsolete
- codebase-enforced? -> archive
- verbose? -> condense
- merge duplicates

### Phase 3: Audit `ITERATION_LOG.md`

- 2+ occurrences -> promote
- proposal for unpromoted insights
- archive 100+ entries to `ITERATION_LOG_ARCHIVE.md`

### Phase 4: Audit Sub-Agents

- unused? -> flag
- stale patterns? -> update
- >100 lines? -> split
- overlapping? -> merge

### Phase 5: Integrity

- zero overlap
- reference valid
- layer placement correct
- sub-agent didn't bypass main agent for writes

### Maintenance Report

```markdown
# Maintenance Report - [YYYY-MM-DD]
Summary: [N] removed/kept/promoted
Changes: [rationale]
Developer flags: [manual decisions]
Health: stats vs targets
```

### Core Invariant

Context = `AGENTS.md` + `LESSONS_LEARNED.md` + sub-agent. Exactly needed. No duplicates. One layer.

---

## References

- Rottger et al. (2026) - AGENTS.md efficacy
- Li et al. (2026) - SkillsBench
- Yu et al. (2026) - Architecture perspective
- everything-claude-code patterns
