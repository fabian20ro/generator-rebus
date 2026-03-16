/**
 * Renders the clue lists (Horizontal / Vertical).
 *
 * Optimised: DOM elements are created once per clue set and then
 * diff-patched (class toggles only) on subsequent calls.
 */

import type { Clue } from "../db/puzzle-repository";
import type { GridState } from "./grid-renderer";

/** Tracks the <li> elements that were created for a given container. */
const panelCache = new WeakMap<
  HTMLElement,
  { count: number; items: HTMLLIElement[]; clueIds: string[] }
>();

export function renderClues(
  hContainer: HTMLElement,
  vContainer: HTMLElement,
  state: GridState,
  onClueClick: (clue: Clue) => void
): void {
  const hClues = state.clues.filter((c) => c.direction === "H");
  const vClues = state.clues.filter((c) => c.direction === "V");

  renderClueList(hContainer, hClues, state, onClueClick);
  renderClueList(vContainer, vClues, state, onClueClick);
}

function renderClueList(
  container: HTMLElement,
  clues: Clue[],
  state: GridState,
  onClueClick: (clue: Clue) => void
): void {
  const cached = panelCache.get(container);

  // Rebuild DOM only if the clue set changed (different count or ids)
  const needsRebuild =
    !cached ||
    cached.count !== clues.length ||
    clues.some((clue, i) => cached.clueIds[i] !== clue.id);

  if (needsRebuild) {
    container.innerHTML = "";
    const items: HTMLLIElement[] = [];
    const clueIds: string[] = [];

    for (const clue of clues) {
      const li = document.createElement("li");
      li.className = "clue-item";
      li.dataset.clueId = clue.id;
      li.value = clue.clue_number;
      li.textContent = `${clue.clue_number}. ${clue.definition}`;

      li.addEventListener("click", () => onClueClick(clue));
      container.appendChild(li);

      items.push(li);
      clueIds.push(clue.id);
    }

    panelCache.set(container, { count: clues.length, items, clueIds });
  }

  // Diff-patch: update classes on existing elements
  const entry = panelCache.get(container)!;

  for (let i = 0; i < clues.length; i++) {
    const clue = clues[i];
    const li = entry.items[i];

    const isComplete = isClueComplete(clue, state);
    const isActive =
      state.activeDirection === clue.direction &&
      isInClue(clue, state.activeRow, state.activeCol);

    li.classList.toggle("clue-item--complete", isComplete);
    li.classList.toggle("clue-item--active", isActive);
  }

  // Scroll active clue into view
  const activeLi = entry.items.find((li) =>
    li.classList.contains("clue-item--active")
  );
  if (activeLi) {
    activeLi.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function isClueComplete(clue: Clue, state: GridState): boolean {
  for (let i = 0; i < clue.length; i++) {
    const r = clue.direction === "H" ? clue.start_row : clue.start_row + i;
    const c = clue.direction === "H" ? clue.start_col + i : clue.start_col;
    if (!state.cells[r][c] || state.cells[r][c] === "#") {
      return false;
    }
  }
  return true;
}

function isInClue(clue: Clue, row: number, col: number): boolean {
  if (clue.direction === "H") {
    return (
      row === clue.start_row &&
      col >= clue.start_col &&
      col < clue.start_col + clue.length
    );
  } else {
    return (
      col === clue.start_col &&
      row >= clue.start_row &&
      row < clue.start_row + clue.length
    );
  }
}
