# AI Agent Config Setup Guide

> Two roles: (1) setup from scratch, (2) periodic maintenance - hand to agent, it audits + cleans all files.
> Use when: new project, existing project onboard, periodic hygiene (weekly/monthly/yearly).

---

## Research Context

- **[Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988)** - LLM-generated context files (`/init`): -3% task success, +20% cost. Developer-provided: +4% marginal. Context files -> broader but less focused exploration.
- **[SkillsBench](https://arxiv.org/abs/2602.12670)** - Curated focused skills (2-3 modules) outperform comprehensive docs. Self-generated skills: zero benefit. Smaller models + good skills ~= larger models without.
- **[Yu et al. 2026](https://arxiv.org/abs/2602.12670)** - Multi-agent memory as computer architecture problem. Three-layer hierarchy. Cache sharing + access control = critical protocol gaps. Memory consistency = hardest open challenge.

**Core principle:** help model, don't distract. Info discoverable from codebase -> don't repeat in config.

---

## File Synchronization Model

Four file types. Distinct roles. Overlap = bug.

```text
AGENT STARTS TASK
  1. Read AGENTS.md (bootstrap - always in context, smallest possible)
     -> constraints, legacy traps, file references
     -> pointer to LESSONS_LEARNED.md + sub-agents

  2. Read LESSONS_LEARNED.md (curated wisdom)
     -> validated corrections + patterns from past sessions
     -> "agent keeps doing X wrong" lives HERE not AGENTS.md

  3. Complex task? -> delegate to sub-agent
     -> .claude/agents/architect.md, planner.md, etc.
     -> focused procedural knowledge, loaded on demand

  4. Do work

  5. End of iteration:
     -> ALWAYS append ITERATION_LOG.md
     -> reusable insight? -> add LESSONS_LEARNED.md
     -> surprise? -> flag to developer

  Developer decides:
     -> fix codebase? (preferred)
     -> add LESSONS_LEARNED? (if codebase fix impossible)
     -> add AGENTS.md? (only non-discoverable pre-exploration constraint)
     -> new sub-agent? (invoke agent-creator)

  PERIODIC MAINTENANCE (weekly/monthly/yearly/new model):
     -> hand this document to agent as standalone task
     -> agent audits ALL files, removes stale, promotes patterns
     -> files get leaner - never fatter
```

### Boundary rules

| Question | -> File |
|----------|---------|
| Discoverable from codebase? | **Nowhere** |
| Constraint needed BEFORE exploring? | **AGENTS.md** |
| Correction for repeated mistake? | **LESSONS_LEARNED.md** |
| Raw observation, single session? | **ITERATION_LOG.md** |
| Focused procedural knowledge, recurring domain? | **Sub-agent** `.claude/agents/` |
| Codebase keeps confusing agents? | **Fix codebase first** -> then `LESSONS_LEARNED.md` if needed |

### Memory hierarchy (Yu et al. 2026)

Maps file system to three-layer architecture. Clarifies why each file exists.

```text
LAYER             HARDWARE ANALOGY       OUR FILES
--------------------------------------------------------
Boot / ROM        BIOS, firmware         AGENTS.md
                  Read at startup.       Read once. Smallest. Static.
                  Rarely changes.

Cache             L1/L2 cache            Current session context
                  Fast, limited,         Conversation, tool calls,
                  volatile.              working files. NOT persisted.

Shared memory     Main RAM               LESSONS_LEARNED.md
                  All processors read.   All agents read at start.
                  Needs consistency.     Written end of iteration.

Write-ahead log   Transaction log        ITERATION_LOG.md
                  Append-only.           Append-only. Never deleted.
                  Crash recovery.        Source of truth for history.

Local memory      Per-core scratchpad    Sub-agents (.claude/agents/)
                  Processor-specific.    Domain-specific. Loaded on demand.

Disk / storage    SSD, persistent        Codebase itself
                                         Agents explore on demand.
                                         NEVER duplicate into config.
```

**Key insight:** performance problems = data stuck in wrong layer. Correction in `AGENTS.md` (ROM) that belongs in `LESSONS_LEARNED.md` (RAM) = variable stored in firmware instead of memory - can't update without reflash.

### Access control

| File | Main agent | Sub-agents | Maintenance agent | Developer |
|------|------------|------------|-------------------|-----------|
| `AGENTS.md` | **Read** | **Read** | **Read+Write** (audit) | **Write** (authority) |
| `LESSONS_LEARNED.md` | **Read+Write** | **Read** only | **Read+Write** (audit) | **Write** (authority) |
| `ITERATION_LOG.md` | **Append** | **Append** | **Read** | **Read** |
| Sub-agent files | **Read** | **Read** own only | **Read+Write** (audit) | **Write** (authority) |

Rules:
- Sub-agents never modify `AGENTS.md` or `LESSONS_LEARNED.md` directly -> report to main agent
- Only main agent or developer adds to `LESSONS_LEARNED.md` -> prevents conflicting lessons
- `ITERATION_LOG.md` = only file sub-agents append directly (append-only, conflict-free)
- Maintenance agent = only automated write access to `AGENTS.md` (audit only, reported)

### Consistency model (concurrent sessions)

**`ITERATION_LOG.md`** - no conflicts possible. Append-only. Git merges cleanly.

**`LESSONS_LEARNED.md`** - last-writer-wins + review. Concurrent promotions -> git conflict -> keep both, flag for review next maintenance.

**`AGENTS.md`** - immutable between maintenance cycles. Agents don't write during work. Zero concurrent write risk.

**Sub-agents** - immutable during sessions. Change only during maintenance or `agent-creator` invocation.

**Rule:** frequent merge conflicts in config files -> too many writers -> architecture wrong.

### Promotion flow

```text
Observation (single session) -> ITERATION_LOG.md (always)
Same issue 2+ times in log -> promote to LESSONS_LEARNED.md
Lesson obsolete -> Archive in LESSONS_LEARNED (never delete)
New recurring domain -> agent-creator -> sub-agent in .claude/agents/
New model release -> delete AGENTS.md, test, re-add only what breaks
                  -> review LESSONS_LEARNED.md - archive what model handles now
Periodic maintenance -> hand this doc to agent, full audit
```

---

## File Structure

```text
project-root/
|- AGENTS.md                 # bootstrap (minimal, non-discoverable only)
|- CLAUDE.md                 # redirect -> AGENTS.md
|- GEMINI.md                 # redirect -> AGENTS.md
|- LESSONS_LEARNED.md        # curated corrections + validated wisdom
|- ITERATION_LOG.md          # append-only session journal
|- SETUP_AI_AGENT_CONFIG.md  # setup + maintenance protocol
`- .claude/
   `- agents/
      |- architect.md        # system design, ADRs
      |- planner.md          # multi-step implementation plans
      |- agent-creator.md    # meta-agent: creates new agents
      `- ux-expert.md        # UI/UX (frontend projects only)
```

---

## Step 1: `CLAUDE.md` and `GEMINI.md`

Entire file content:

```markdown
Read AGENTS.md asap
```

Nothing else. Redirect for tools expecting `CLAUDE.md` or `GEMINI.md`

---

## Step 2: `AGENTS.md`

Always in context window. Every token costs attention on every request. Only non-discoverable pre-exploration info.

### Does NOT belong

- architecture overviews (model reads file tree, imports, configs)
- dependency lists (model reads `package.json`, `pom.xml`, `requirements.txt`)
- scripts/commands (model reads `package.json` scripts, `Makefile`)
- folder structure (model uses `find`, `ls`, `rg`)
- code style rules linter enforces
- general best practices model knows (`SOLID`, `DRY`)
- anything from `/init` - delete it
- corrections for repeated mistakes -> `LESSONS_LEARNED.md`

### DOES belong

- non-obvious tooling constraints ("use pnpm not npm - workspaces break")
- environment assumptions ("dev server already running - don't start")
- legacy traps misleading on first encounter ("TRPC in /api/legacy/ deprecated - use Convex")
- references to `LESSONS_LEARNED.md`, `ITERATION_LOG.md`, sub-agents

### Mandatory Preamble

All `AGENTS.md` files MUST begin with this exact block:

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

`AGENTS.md` shrinks over time, never grows:
- monthly audit: delete entries model no longer needs
- new model release: delete entirely, test, re-add only what breaks
- never `/init`: auto-generated worse than nothing (-3% success, +20% cost)
- prefer fixing codebase over adding entries

---

## Step 3: Sub-Agents `.claude/agents/`

Focused procedural knowledge. Per SkillsBench: curated 2-3 module skills -> +16.2pp pass rate. Self-generated -> zero benefit.

### `architect.md`

````markdown
# Architect

system design, scalability, technical decisions.

## When to Activate

Proactively when:
- new feature touches 3+ modules
- refactoring large system / changing data flow
- technology selection
- creating/updating ADRs

## Role

senior software architect. think holistically before code. prioritize: simplicity, changeability, clear boundaries, obvious data flow.

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
Current: [how it works now]
Proposed: [how it should work]
Migration: [steps, reversible if possible]
Risk: [what could go wrong]
Affected: [modules]
```

## Principles

- simplest solution that works. complexity requires justification.
- record every decision as ADR.
- changing A requires changing B -> design smell.
- composition > inheritance. functions > classes unless state needed.
````

### `planner.md`

````markdown
# Planner

implementation planning for complex features + multi-step work.

## When to Activate

Proactively when:
- feature spans 3+ files
- specific step ordering required
- previous attempt failed (plan retry)
- new feature request (plan before code)

## Role

break down complex work -> small verifiable steps. produce plan, never code directly.

## Output Format

```text
# Plan: [Feature]

## Overview
[2-3 sentences: what + why]

## Prerequisites
- [ ] [must be true before starting]

## Phases

### Phase 1: [Name] (est: N files)
1. **[Step]** - `path/to/file`
   - action: [specific]
   - verify: [how to confirm]
   - depends: none / step X

### Phase 2: [Name]
...

## Verify
- [ ] end-to-end check
- [ ] type check / lint pass
- [ ] tests pass

## Rollback
[undo steps]
```

## Principles

- every step must have verification. can't verify? -> break down further.
- 1-3 files per phase max.
- front-load riskiest step. fail fast.
- retry? plan must address WHY previous attempt failed.
````

### `ux-expert.md`

````markdown
# UX Expert

frontend design decisions, component architecture, interaction patterns.

## When to Activate

Proactively when:
- new UI components/pages
- interaction flow evaluation
- accessibility decisions
- UI pattern choice (modal vs drawer, tabs vs accordion)
- responsive layout decisions

## Role

senior UX engineer. bridge design <-> implementation. think about real human interaction.

## Output Format

### Component
```text
## Component: [Name]
User goal: [what user accomplishes]
Interaction: [how user interacts]
States: empty / loading / populated / error / disabled
A11y: keyboard [method] / screen reader [announced] / ARIA [roles]
Responsive: [mobile / tablet / desktop diffs]
Edge cases: [long text, many items, no items]
```

### Flow
```text
## Flow: [Name]
Entry: [where user starts]
Happy path: [steps]
Error paths: [what goes wrong + recovery]
Feedback: [what user sees each step]
```

## Principles

- every interactive element: keyboard accessible.
- loading + error states: not optional - design first.
- empty states = UX opportunity.
- animations: respect `prefers-reduced-motion`.
- mobile /= smaller desktop. touch targets min 44px, thumb zones.
````

### `agent-creator.md`

````markdown
# Agent Creator

meta-agent. designs + creates new specialized sub-agents.

## When to Activate

- recurring task domain needs focused expertise
- developer requests new agent
- existing agent scope too broad -> split

## Reference Archetypes

existing agents in `.claude/agents/` for structure.
more patterns: https://github.com/affaan-m/everything-claude-code/tree/main/agents

| Archetype | For | Source |
|-----------|-----|--------|
| architect | system design, ADRs | this project |
| planner | implementation plans | this project |
| ux-expert | frontend UI/UX | this project |
| code-reviewer | quality, security | everything-claude-code |
| tdd-guide | test-driven dev | everything-claude-code |
| security-reviewer | vulnerabilities, OWASP | everything-claude-code |
| build-error-resolver | CI/build failures | everything-claude-code |
| e2e-runner | end-to-end tests | everything-claude-code |
| refactor-cleaner | dead code, cleanup | everything-claude-code |
| doc-updater | docs freshness | everything-claude-code |
| database-reviewer | schema, query optimization | everything-claude-code |

## Design Rules

**1. Focus: 2-3 modules max.** Per SkillsBench: focused > comprehensive. Agent covering everything helps nothing.

**2. Mandatory structure:**
```text
# [Name]
[one-line description]

## When to Activate
Proactively when: [3+ triggers]

## Role
[specific role, what you do / don't do]

## Output Format
[concrete templates, fenced code blocks, placeholder fields]

## Principles
[3-5 actionable, not platitudes]
```

**3. Anti-patterns:**
- info model already knows
- duplicate `AGENTS.md` or `LESSONS_LEARNED.md`
- overlapping agents - merge instead
- one-off tasks - agents for recurring work only
- >100 lines - scope too broad

**4. Registration:** update Sub-Agents table in `AGENTS.md` after creating.

## Output

1. `.md` file content
2. path: `.claude/agents/[kebab-case].md`
3. `AGENTS.md` row: `| [Name] | .claude/agents/[name].md | [when - one line] |`

## Validation

- [ ] 3+ triggers in "When to Activate"
- [ ] concrete output template
- [ ] 3-5 actionable principles
- [ ] no codebase-discoverable info
- [ ] no overlap with existing agents
- [ ] scope <= 2-3 modules
- [ ] <= 100 lines
- [ ] `AGENTS.md` table updated
````

### Inter-agent handoff protocol

Sub-agent output -> main agent. Analogous to cache-to-cache transfer (Yu et al. 2026). Must be self-contained - receiving agent acts without re-reading sub-agent's full context.

```text
## Handoff: [Sub-agent] -> [Receiver]
Task: [what sub-agent was asked]
Result: [concrete output - ADR, plan, spec]
Artifacts: [file paths written to disk]
Open questions: [unresolved]
Next step: [what + who]
```

Rules:
- main agent = orchestrator. Sub-agents report back, never to each other directly.
- sub-agent A output needed by B? -> main agent passes relevant artifact, not full context.
- decisions (ADRs, plans) -> persist to disk, survive session boundaries.
- observations (smells, surprises) -> main agent appends to `ITERATION_LOG.md`.

---

## Step 4: `LESSONS_LEARNED.md`

Curated knowledge base. Validated corrections + reusable insights. "Agent keeps doing X wrong -> do Y instead" lives HERE. Not `AGENTS.md`.

```markdown
# Lessons Learned

> maintained by AI agents. validated, reusable insights.
> **read start of every task. update end of every iteration.**

## How to Use

- **start of task:** read before writing code - avoid known mistakes
- **end of iteration:** new reusable insight? -> add to appropriate category
- **promotion:** pattern 2+ times in `ITERATION_LOG.md` -> promote here
- **pruning:** obsolete -> Archive section (date + reason). never delete.

---

## Architecture & Design Decisions
<!-- **[YYYY-MM-DD]** title - explanation -->

## Code Patterns & Pitfalls
<!-- **[YYYY-MM-DD]** title - explanation -->

## Testing & Quality
<!-- **[YYYY-MM-DD]** title - explanation -->

## Performance & Infrastructure
<!-- **[YYYY-MM-DD]** title - explanation -->

## Dependencies & External Services
<!-- **[YYYY-MM-DD]** title - explanation -->

## Process & Workflow
<!-- **[YYYY-MM-DD]** title - explanation -->

---

## Archive
<!-- **[YYYY-MM-DD] Archived [YYYY-MM-DD]** title - reason -->
```

---

## Step 5: `ITERATION_LOG.md`

Raw append-only journal. Source of truth for what happened. Patterns promoted to `LESSONS_LEARNED.md`.

```markdown
# Iteration Log

> append-only. entry end of every iteration.
> same issue 2+ times? -> promote to `LESSONS_LEARNED.md`.

## Entry Format

---

### [YYYY-MM-DD] Brief Description

**Context:** goal / trigger
**Happened:** key actions, decisions
**Outcome:** success / partial / failure
**Insight:** (optional) what to tell next agent
**Promoted:** yes / no

---

<!-- new entries above this line, most recent first -->
```

---

## Step 6: Git

```bash
git add AGENTS.md CLAUDE.md GEMINI.md LESSONS_LEARNED.md ITERATION_LOG.md SETUP_AI_AGENT_CONFIG.md .claude/agents/ .github/pull_request_template.md
git commit -m "chore: add AI agent config + memory system"
```

PR template addition:

```markdown
## AI Agent Checklist
- [ ] appended ITERATION_LOG.md
- [ ] promoted reusable insights -> LESSONS_LEARNED.md
- [ ] reviewed AGENTS.md - anything to remove? (should shrink, not grow)
```

---

## Verification

- [ ] `CLAUDE.md` = only `Read AGENTS.md asap`
- [ ] `GEMINI.md` = only `Read AGENTS.md asap`
- [ ] `AGENTS.md`: minimal, no `/init` content, no corrections (`LESSONS_LEARNED.md` instead), starts with `work style: telegraph`
- [ ] `AGENTS.md` references `LESSONS_LEARNED.md`, `ITERATION_LOG.md`, sub-agents
- [ ] `.claude/agents/architect.md` exists
- [ ] `.claude/agents/planner.md` exists
- [ ] `.claude/agents/agent-creator.md` exists
- [ ] `.claude/agents/ux-expert.md` exists (frontend repo)
- [ ] `LESSONS_LEARNED.md` exists, empty categories
- [ ] `ITERATION_LOG.md` exists, format template
- [ ] all tracked in git
- [ ] no architecture overviews, folder structures, dependency lists anywhere except this setup guide
- [ ] zero content overlap between `AGENTS.md` and `LESSONS_LEARNED.md`
- [ ] every piece of info in exactly one memory layer

---

## Decision Flowchart

```text
Repeated mistake?
  `- fix codebase? YES -> fix. NO -> log ITERATION_LOG -> 2+ times -> LESSONS_LEARNED

Need to know BEFORE exploring?
  `- discoverable? YES -> nowhere. NO -> AGENTS.md Constraints (one line)

Complex multi-step? -> invoke planner before code
Architecture decision? -> invoke architect -> ADR
Frontend component? -> invoke ux-expert
Recurring domain, no agent? -> invoke agent-creator
New model? -> delete AGENTS.md, test, re-add only what breaks
           -> archive obsolete LESSONS_LEARNED entries
AGENTS.md growing? -> wrong. move corrections -> LESSONS_LEARNED. delete discoverable.
Maintenance time? -> hand this doc to agent: "Run Periodic Maintenance Protocol"
```

---

## Periodic Maintenance Protocol

Standalone task. Hand this document to agent: *"Run maintenance protocol on this project."* Agent audits + cleans all files without further guidance.

### Frequency

| Cadence | Trigger |
|---------|---------|
| weekly | active project, many `ITERATION_LOG.md` entries |
| monthly | default, steady projects |
| per model release | major cleanup opportunity |
| yearly | dormant project, before resuming |

### Phase 1: Audit `AGENTS.md`

Goal: as small as possible. Every line earns its place.

```text
Each "Constraints" entry:
  1. discoverable from codebase? (package.json, Makefile, tsconfig, CI) -> REMOVE
  2. still accurate? constraint still applies? -> KEEP or FIX
  3. inaccurate -> REMOVE

Each "Legacy & Deprecated" entry:
  1. legacy code/routes still exist? -> KEEP
  2. already removed from codebase? -> REMOVE

Contains corrections/patterns? -> MOVE to LESSONS_LEARNED
Sub-agents table matches .claude/agents/ dir? -> sync
Still starts with "work style: telegraph"? -> ensure present
```

Success: `AGENTS.md` shorter after audit. Never longer.

### Phase 2: Audit `LESSONS_LEARNED.md`

Goal: every lesson still relevant, not duplicated.

```text
Each lesson:
  1. still accurate? (dependency changed? API updated? model fixed?) -> obsolete? ARCHIVE
  2. now enforced by codebase? (linter, test, type check) -> ARCHIVE ("enforced by [X]")
  3. duplicated in AGENTS.md? -> REMOVE from AGENTS.md
  4. too verbose? -> CONDENSE
  5. multiple lessons say same thing? -> MERGE

Category health:
  20+ entries in one category? -> possibly too granular
  empty category after 3+ months? -> flag (not necessarily problem)
```

Success: every remaining lesson passes: *"would new agent benefit from this TODAY?"*

### Phase 3: Audit `ITERATION_LOG.md`

Goal: append-only, but patterns promoted.

```text
Entries since last maintenance:
  1. repeated issues (same problem 2+ entries)? -> promote to LESSONS_LEARNED if not already
  2. valuable unpromoted insights? -> propose promotion

Log size:
  100+ entries? -> archive older (>1 month) to ITERATION_LOG_ARCHIVE.md
  (archive = still append-only, still git-tracked)
```

Success: zero unhandled patterns sitting for 2+ cycles.

### Phase 4: Audit Sub-Agents

Goal: focused, current, earning their keep.

```text
Each agent:
  1. still invoked? (check ITERATION_LOG refs) -> unused 3+ months? FLAG
  2. references stale tools/patterns? -> UPDATE or REMOVE
  3. over 100 lines? -> SPLIT or CONDENSE
  4. overlaps another agent? -> MERGE or clarify boundaries

Recurring tasks in ITERATION_LOG no agent covers?
  -> propose new agent (don't auto-create)
```

### Phase 5: Cross-File Consistency & Hierarchy Integrity

Goal: zero overlap, zero contradictions, correct layer placement.

```text
Content overlap:
  corrections in both AGENTS.md + LESSONS_LEARNED? -> remove from AGENTS.md
  same constraint in AGENTS.md + sub-agent? -> one place only
  sub-agent principle contradicts lesson? -> FLAG

Reference integrity:
  AGENTS.md sub-agents table = .claude/agents/ dir?
  AGENTS.md mentions LESSONS_LEARNED + ITERATION_LOG?
  all file paths valid?

Memory hierarchy placement:
  AGENTS.md (ROM) has content belonging in LESSONS_LEARNED (RAM)?
    signs: correction, changes frequently, learned from experience -> MOVE
  LESSONS_LEARNED has content belonging in AGENTS.md?
    signs: pre-exploration constraint, immediate failure without -> MOVE
  config files have content codebase now provides?
    signs: linter rule, test, type system -> REMOVE

Access control:
  sub-agent directly modified AGENTS.md or LESSONS_LEARNED? -> FLAG
  ITERATION_LOG entries properly attributed? -> check
  lessons without corresponding ITERATION_LOG entries? -> FLAG

Concurrent sessions (worktrees / parallel agents):
  merge conflicts since last maintenance?
    ITERATION_LOG: auto-resolve (keep both, reorder by date)
    LESSONS_LEARNED: keep both, flag duplicates
    AGENTS.md: shouldn't happen -> investigate
```

### Maintenance Report

```markdown
# Maintenance Report - [YYYY-MM-DD]

## Summary
- AGENTS.md: [N] removed, [N] kept, [N] corrected
- LESSONS_LEARNED.md: [N] archived, [N] condensed, [N] merged, [N] kept
- ITERATION_LOG.md: [N] patterns promoted, [N] entries since last maintenance
- Sub-agents: [N] updated, [N] flagged, [N] unchanged

## Changes Made
<!-- each change + rationale -->

## Flagged for Developer
<!-- things needing human decision -->

## Health Score
- AGENTS.md: [N] lines (target: <30)
- LESSONS_LEARNED active entries: [N] (target: <50 per category)
- ITERATION_LOG unprocessed: [N] (target: 0 patterns unhandled)
- Sub-agents: [N] (warning >8 = likely overlap)
- Cross-file duplicates: [N] (target: 0)
- Memory hierarchy violations: [N] (target: 0)
```

### Core Invariant

> agent reading `AGENTS.md` -> `LESSONS_LEARNED.md` -> relevant sub-agent = exactly the context needed. no more. no less. nothing duplicated. nothing stale. nothing codebase already provides. every piece of info in exactly one memory layer.

Maintenance agent can't confirm? -> flag specific violations.

---

## References

- Rottger et al. (2026). *Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?* [arxiv.org/abs/2602.11988](https://arxiv.org/abs/2602.11988)
- Li et al. (2026). *SkillsBench: Benchmarking How Well Agent Skills Work Across Diverse Tasks.* [arxiv.org/abs/2602.12670](https://arxiv.org/abs/2602.12670)
- Yu et al. (2026). *Multi-Agent Memory from a Computer Architecture Perspective.* Architecture 2.0 Workshop, Pittsburgh.
- Affaan M. *everything-claude-code* - agents, skills, hooks. [github.com/affaan-m/everything-claude-code](https://github.com/affaan-m/everything-claude-code/tree/main/agents)
