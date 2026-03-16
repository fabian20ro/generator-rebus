# Lessons Learned

> maintained by AI agents. validated, reusable insights.
> **read start of every task. update end of every iteration.**

## How to Use

- **start of task:** read before writing code — avoid known mistakes
- **end of iteration:** new reusable insight? → add to appropriate category
- **promotion:** pattern 2+ times in `ITERATION_LOG.md` → promote here
- **pruning:** obsolete → Archive section (date + reason). never delete.

---

## Architecture & Design Decisions

**[2026-03-14]** Two-model architecture prevents self-reinforcing hallucinations — when same LLM rates its own definitions, it agrees with itself. Alternate gpt-oss-20b and eurollm-22b across rewrite rounds. Model B rates Model A's work. Cross-model verification broke the feedback loop.

## Code Patterns & Pitfalls

**[2026-03-14]** Short words (OU, AT, OF) need special handling — for 2-letter words, any definition almost inevitably contains the answer. English homograph hints inject correct Romanian meaning. Preset definitions (AT, OF) bypass LLM entirely. `_definition_describes_english_meaning()` guard rejects English-meaning definitions.

**[2026-03-14]** Family check needs prefix stripping — `clue_uses_same_family` only stripped suffixes. Prefixed words (NEINCEPUT→ÎNCEPUT) weren't caught. Added `ROMANIAN_PREFIXES` list, `forbidden_definition_stems()`, and `_family_exclusion_note()` in prompt builders.

## Testing & Quality
<!-- **[YYYY-MM-DD]** title — explanation -->

## Performance & Infrastructure
<!-- **[YYYY-MM-DD]** title — explanation -->

## Dependencies & External Services
<!-- **[YYYY-MM-DD]** title — explanation -->

## Process & Workflow
<!-- **[YYYY-MM-DD]** title — explanation -->

---

## Archive
<!-- **[YYYY-MM-DD] Archived [YYYY-MM-DD]** title — reason -->
