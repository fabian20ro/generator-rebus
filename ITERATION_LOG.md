# Iteration Log

> append-only. entry end of every iteration.
> same issue 2+ times? → promote to `LESSONS_LEARNED.md`.

## Entry Format

---

### [YYYY-MM-DD] Brief Description

**Context:** goal / trigger
**Happened:** key actions, decisions
**Outcome:** success / partial / failure
**Insight:** (optional) what to tell next agent
**Promoted:** yes / no

---

### [2026-03-14] Add prefix stripping and forbidden stems to family check

**Context:** TIBETAN burned 8 rewrite rounds because LLM kept using "Tibet". NEINCEPUT-type words not caught by suffix-only family check.
**Happened:** Added Romanian prefix stripping to `clue_family.py`, `forbidden_definition_stems()` function, and `_family_exclusion_note()` in prompt builders. Removed OU/URINARE presets.
**Outcome:** pending observation
**Insight:** prefix stripping + forbidden stems = essential for Romanian morphology in family checks
**Promoted:** yes — see LESSONS_LEARNED "Family check needs prefix stripping"

---

<!-- new entries above this line, most recent first -->
