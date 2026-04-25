## 2026-04-06 - [Vanilla DOM Rendering Optimization: State String Memoization]
**Learning:** Vanilla JS/TS DOM mutations (`grid-renderer.ts`) on every interaction create layout recalculation overhead, even when values do not change. No virtual DOM layer blocks it.
**Action:** For complex grids/lists in vanilla JS, add a small memoization layer. Build a visual-state string (`${isActive}|${isHighlight}|${displayVal}`), cache it, skip DOM touches (`classList.toggle`, `input.value = `) when unchanged.
