# Lessons Learned

## [2026-03-14] Two-model architecture prevents self-reinforcing hallucinations

**Problem:** When the same LLM rates its own definitions, it agrees with itself — wrong definitions get 9/10 scores.
**Solution:** Alternate gpt-oss-20b and eurollm-22b across rewrite rounds. Model B rates Model A's work.
**Why it matters:** Cross-model verification broke the feedback loop and improved definition accuracy measurably.

## [2026-03-14] Short words (OU, AT, OF) need special handling — LLM can't avoid mentioning 2-letter answers

**Problem:** For 2-letter words, any definition almost inevitably contains the answer or a close family form. The LLM also defaults to English meanings for words like AN, OF, AT, IN.
**Solution:** English homograph hints inject correct Romanian meaning into prompts. Preset definitions (AT, OF) bypass LLM entirely. `_definition_describes_english_meaning()` guard rejects English-meaning definitions.
**Why it matters:** Without these guards, 30-50% of short word definitions describe English meanings.

## [2026-03-14] Family check needs prefix stripping — words like NEINCEPUT get stuck

**Problem:** `clue_uses_same_family` only stripped suffixes. Prefixed words (NEINCEPUT→ÎNCEPUT, REINCEPUT→ÎNCEPUT) weren't caught, and the LLM wasn't told which root forms to avoid (TIBETAN→TIBET).
**Solution:** Added `ROMANIAN_PREFIXES` list with prefix stripping in `clue_family.py`. Added `forbidden_definition_stems()` to compute forbidden forms for LLM prompts. Added `_family_exclusion_note()` in `ai_clues.py` to append forbidden words to generate/rewrite prompts.
**Why it matters:** TIBETAN burned 8 rewrite rounds before this fix because every attempt used "Tibet" in the definition.
