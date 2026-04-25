# Future Improvements

Gen 2026-03-16 by architect, crossword-expert, UX agents.
Update 2026-03-16: Drop implemented, keep remaining.

## Not Planned
### Diacritics in grid
- `/[^A-Z]/g` in `grid-renderer.ts` correct.
- Cell "A" shared by "A" (horizontal) and "Ă" (vertical) OK.
- Variants map A-Z by design.

## Remaining
### #12 — Daily challenge
**Effort:** L | **Impact:** 8/10
- Feature puzzle/day + leaderboard.
- Drives retention/social.
- Out of scope; needs backend/API.

### #16 — Split batch_publish.py
**Effort:** L | **Impact:** 6/10
- Score helpers extracted to `core/score_helpers.py`.
- Split: `_rewrite_failed_clues` -> `rewrite_engine.py`, `_prepare_puzzle_for_publication` -> `puzzle_preparation.py`.
- Deferred: Risk to coupled orchestration.
