# Ranked Improvements — Cross-Expert Consensus

Generated 2026-03-16 by architect, crossword-expert, and UX agents.

---

## Top 10

### #1 — Fix diacritics stripping in grid input
**Effort:** S | **Impact:** 10/10 | **Category:** BUG | **Consensus:** Unanimous #1 (all 3 experts)

- `grid-renderer.ts:125` — `/[^A-Z]/g` strips Ă, Â, Î, Ș, Ț
- `input-handler.ts:97` — `/^[A-Za-z]$/` blocks diacritical key presses
- A Romanian crossword app that rejects Romanian characters is fundamentally broken
- Fix: update regexes to include `ĂÂÎȘȚăâîșț` in 2-3 files

### #2 — Eliminate full grid re-render on every keystroke
**Effort:** M | **Impact:** 9/10 | **Category:** PERFORMANCE | **Consensus:** Top 3 (all 3 experts)

- `grid-renderer.ts:65` — `container.innerHTML = ""` destroys and recreates 100-225 DOM nodes per keypress
- Event listeners recreated per render (3 per cell = 675 on 15×15)
- Causes visible flicker, focus loss, and input lag on mobile
- Fix: diff-patch individual cells, use event delegation on container

### #3 — Fix hint revealed-cell rendering
**Effort:** S | **Impact:** 8/10 | **Category:** BUG | **Consensus:** Top 3 (architect + crossword expert)

- `hint-system.ts` writes to `state.cells[r][c]` but never sets `dataset.revealed`
- `grid-renderer.ts:114` checks `dataset.revealed === "true"` — never true
- Players pay points for hints but can't distinguish hinted cells from their own input
- Fix: add `revealed` boolean grid to GridState, set in hint-system, check in renderer

### #4 — Parallel solution fetch with puzzle load
**Effort:** S | **Impact:** 8/10 | **Category:** BUG | **Consensus:** Top 4 (architect + UX expert)

- `main.ts:296` — `getSolution(id)` fires after puzzle loads; hints unavailable for 1-2s
- Fix: `Promise.all([getPuzzle(id), getSolution(id)])` — trivial change
- Disable hint buttons with loading indicator until solution arrives

### #5 — Add CI for Python tests and linting
**Effort:** M | **Impact:** 8/10 | **Category:** INFRA | **Consensus:** Architect #6

- Zero automated quality gates for the generator pipeline that produces every puzzle
- Workflow only triggers on `frontend/**` changes — Python ships unvalidated
- Fix: add workflow with `pytest + ruff + mypy` triggered on `generator/**` and `tests/**`

### #6 — Add tutorial / onboarding for new players
**Effort:** M | **Impact:** 8/10 | **Category:** ENGAGEMENT | **Consensus:** Top 10 (crossword + UX experts)

- Hint costs, scoring, badge mechanics invisible to new players
- "Verifică — 5 pts" is meaningless without context
- Fix: 3-step coach overlay on first launch (tap cell → type letter → use hints)

### #7 — Fix completed-clue contrast (WCAG AA)
**Effort:** S | **Impact:** 7/10 | **Category:** ACCESSIBILITY | **Consensus:** Top 7 (crossword + UX experts)

- `.clue-item--complete { color: #999 }` on white = 2.85:1 contrast ratio
- WCAG AA requires 4.5:1 for normal text
- Fix: change to `#595959` or darker; strikethrough already signals completion

### #8 — Add difficulty filter to puzzle list
**Effort:** S | **Impact:** 7/10 | **Category:** UX | **Consensus:** Crossword expert #5

- No way to browse puzzles by difficulty level
- Beginners get frustrated by hard puzzles; experts get bored by easy ones
- Difficulty field already exists in the data model — just needs filter UI on puzzle-selector

### #9 — Add progress indicator (% complete)
**Effort:** S | **Impact:** 6/10 | **Category:** UX | **Consensus:** Top 10 (crossword + UX experts)

- Players have no sense of how much puzzle remains
- Fix: simple "32/45 letters" counter or percentage bar in header
- Data already available from `gridState.cells`

### #10 — Debounce progress saving
**Effort:** S | **Impact:** 6/10 | **Category:** PERFORMANCE | **Consensus:** Top 10 (architect + UX expert)

- `saveCurrentProgress()` fires on every keystroke (main.ts lines 162, 173)
- Fast typing triggers 5-10 localStorage writes/second — jank on low-end Android
- Fix: 500ms debounce wrapper; `beforeunload` handler guarantees no data loss

---

## Honorable Mentions (11-20)

| Rank | Improvement | Effort | Impact | Source |
|------|------------|--------|--------|--------|
| 11 | Add undo/redo | M | 8 | Crossword expert |
| 12 | Daily challenge / puzzle of the day | L | 8 | Crossword + UX experts |
| 13 | ARIA labels for screen readers | M | 7 | UX expert |
| 14 | Tests for pipeline_state.py and entry points | M | 7 | Architect |
| 15 | Fix schema.sql missing verify_note/verified columns | S | 7 | Architect |
| 16 | Split batch_publish.py (1140-line monolith) | L | 6 | Generator analysis |
| 17 | Fix upload.py clue matching assumption | S | 6 | Generator analysis |
| 18 | Extract duplicated score functions to core/ | S | 5 | Generator analysis |
| 19 | Add pencil / tentative-answer mode | M | 6 | Crossword expert |
| 20 | Fix badges.ts bounds check on solved array | S | 5 | Frontend analysis |

---

## Expert Perspectives

### Architect Priority
Bugs first (diacritics, hints, solution fetch), then infrastructure (CI, tests), then performance (re-render, debounce). Features are deprioritized until the foundation is solid.

### Crossword Expert Priority
Player-facing bugs first (diacritics, hints), then UX features that drive satisfaction (undo, difficulty filter, progress), then retention hooks (daily challenge, onboarding).

### UX Expert Priority
Critical bugs first (diacritics, re-render, solution fetch), then first-time experience (onboarding), then accessibility (contrast, ARIA), then mobile polish (debounce, modal sizing).

### Common Theme
All three experts agree: **fix the 3 bugs first** (#1 diacritics, #3 hints, #4 solution fetch), **then the performance issue** (#2 re-render), **then build on a solid foundation** with CI, onboarding, and UX features.
