# Iteration Log

Record decisions, changes, and outcomes here. Format:

```
## [YYYY-MM-DD] Title
**Change:** What changed
**Rationale:** Why
**Outcome:** Result (fill in after observing)
```

---

## [2026-03-14] Add prefix stripping and forbidden stems to family check

**Change:** Added Romanian prefix stripping to `clue_family.py`, `forbidden_definition_stems()` function, and `_family_exclusion_note()` in prompt builders. Removed OU/URINARE presets.
**Rationale:** TIBETAN burned 8 rewrite rounds because the LLM kept using "Tibet". NEINCEPUT-type words weren't caught by suffix-only family check.
**Outcome:** (pending observation)
