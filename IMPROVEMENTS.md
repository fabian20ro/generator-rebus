# Future Improvements

Generated 2026-03-16 by architect, crossword-expert, and UX agents.
Updated 2026-03-16: removed implemented items, kept only what remains.

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

- Surface a featured puzzle per day with shared leaderboard
- Drives daily return visits and social sharing
- Too large for current scope — requires backend scheduling + API endpoint

### #16 — Split batch_publish.py further
**Effort:** L | **Impact:** 6/10 | **Source:** Generator analysis

- Score helpers already extracted to `core/score_helpers.py` (1140 → 1036 lines)
- Remaining split: extract `_rewrite_failed_clues` → `rewrite_engine.py`, extract `_prepare_puzzle_for_publication` → `puzzle_preparation.py`
- Deferred: high risk of breaking the tightly coupled orchestration logic
