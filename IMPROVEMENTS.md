# Future Improvements

Generated 2026-03-16 by architect, crossword-expert, and UX agents.
Updated 2026-03-16: implemented items removed, only remaining items kept.

---

## Not Planned (Intentional)

### Diacritics in grid input
- `/[^A-Z]/g` in grid-renderer.ts is **correct Romanian rebus behavior**
- At grid intersections, horizontal might use "A" while vertical uses "Ă" — they share the same cell
- All diacritical variants map to A-Z in the grid by design

---

## Remaining

### #12 — Daily challenge / puzzle of the day
**Effort:** L | **Impact:** 8/10 | **Source:** Crossword + UX experts

- Surface one featured puzzle per day with shared leaderboard
- Drives daily return visits and social sharing
- Too large for current scope; needs backend scheduling + API endpoint

### #16 — Split batch_publish.py further
**Effort:** L | **Impact:** 6/10 | **Source:** Generator analysis

- Score helpers already extracted to `core/score_helpers.py` (1140 → 1036 lines)
- Remaining split: `_rewrite_failed_clues` → `rewrite_engine.py`, `_prepare_puzzle_for_publication` → `puzzle_preparation.py`
- Deferred: high risk of breaking tightly coupled orchestration logic
