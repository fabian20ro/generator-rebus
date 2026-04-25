# Results3 Campaign Review

Source data:
- `generator/assessment/results3.tsv`
- `logs/results_exp150.json`
- `logs/results_exp150_logs/exp*.log`

Scope:
- 99 completed experiments (`exp001`..`exp099`)
- baseline composite: `70.4`
- peak composite reached: `75.0`
- keeps: `6`
- discards: `93`

Operational caveat:
- live git commits, prompt backups, and `results3.tsv` did not stay perfectly aligned; use this report for score signal, but treat the current prompt tree as separate baseline verified from files/backups

## What Actually Worked

Robust keeps worth preserving:
- `exp007` `system/verify.md` `+1.0` — verb/form awareness in verify
- `exp009` `system/verify.md` `+2.2` — longer concrete example in verify
- `exp089` `system/definition.md` `+1.0` — negative example for vague definitions

Weak keeps that may partly be noise and should be retested before treating as permanent truth:
- `exp018` `system/verify.md` `+0.1` — domain-switching awareness
- `exp021` `system/verify.md` `+0.1` — diacritics awareness
- `exp068` `system/rate.md` `+0.2` — tighter guessability scale

Observed pattern:
- verify-system changes were the only clearly productive area
- definition changes mostly hurt, except when they added a negative example against vagueness
- rate changes were usually harmful, but small guessability calibration may help
- user prompt additions were consistently weak or destructive

## Near Misses

These are reasonable retry candidates with lighter wording or paired support from another prompt:
- `exp038` `system/definition.md` `-0.2` — no-ambiguity reinforcement
- `exp075` `user/rate.md` `-0.3` — evaluation framing in user/rate
- `exp002` `system/verify.md` `0.0` — common-word preference
- `exp006` `system/verify.md` `0.0` — exact-sense guard
- `exp072` `system/rate.md` `-1.0` — stronger JSON-only instruction
- `exp028` `system/definition.md` `-1.4` — domain-surprise rule
- `exp055` `user/generate.md` `-1.6` — specificity instruction
- `exp022` `system/verify.md` `-1.6` — confidence instruction
- `exp030` `system/definition.md` `-1.9` — distinctive-sense rule
- `exp052` `user/generate.md` `-1.9` — single-sense instruction
- `exp059` `system/rate.md` `-1.9` — uniqueness criterion in guessability
- `exp078` `system/rate.md` `-1.9` — guessability-first ordering

Interpretation:
- anything within about `0.0 .. -0.5` composite is close enough to be noise-sensitive
- anything around `-1.0 .. -2.0` is only worth retrying if simplified, paired, or repeated
- near-miss retries should be sparse; do not flood the next campaign with the same idea

## Complete Failures

Do not retry these in the same form:
- `exp096` `user/generate.md` — test-question framing
- `exp048` `user/generate.md` — rebus-style instruction
- `exp079` `user/rate.md` — rebus-vs-dictionary note
- `exp098` `system/definition.md` — rewrite-awareness in definition
- `exp092` `user/generate.md` — word-type awareness in generate user
- `exp040` `system/definition.md` — expert-role framing
- `exp095` `system/definition.md` — stronger Romanian-only framing
- `exp033` `system/definition.md` — anti-generic formula rule
- `exp070` `system/rate.md` — removing rarity tolerance / making rate stricter
- `exp074` `system/rate.md` — creative-but-accurate emphasis
- `exp056` `system/rate.md` — grammatical-agreement penalty phrased in rate
- `exp015` `user/verify.md` — rebus-style hint

Observed pattern:
- meta-framing, role inflation, and extra evaluator ceremony usually hurt
- user prompt expansions are especially dangerous
- rate prompt strictness changes can collapse pass rate even when semantics stay acceptable
- definition prompt does not tolerate much extra theory; negative examples beat abstract rules

## Retry Guidance

Retry or rephrase:
- exact-sense / exact-surface-form handling
- anti-ambiguity handling in definition
- guessability calibration in rate
- minimal specificity nudges in `user/generate.md`
- wording that helps verify handle inflected or non-canonical forms

Do not retry soon:
- heavy creativity framing in rate
- extra role/expert/status language
- long multi-example blocks
- extra theory inside user prompts
- stricter rarity penalties
- “rebus style” hints in user-side prompts

## Next Campaign Principles

- start with removals and simplifications; definition/rate likely have excess instruction mass
- alternate across prompt files to reduce overfitting to the currently unchanged prompts
- keep user prompts very short; prefer single-clause edits there
- use only a minority of paired multi-file experiments
- pair prompts only when the same concept must align across generator/evaluator:
  - definition + rate for exact form / ambiguity
  - definition + rewrite for honesty / `[NECLAR]`
  - verify + user/verify for exact-form answering
