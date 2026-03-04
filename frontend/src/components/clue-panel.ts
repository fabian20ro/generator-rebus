/**
 * Renders the clue lists (Horizontal / Vertical).
 */

import type { Clue } from "../db/puzzle-repository";
import type { GridState } from "./grid-renderer";

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
  container.innerHTML = "";

  for (const clue of clues) {
    const li = document.createElement("li");
    li.className = "clue-item";
    li.dataset.clueId = clue.id;
    li.value = clue.clue_number;

    // Check if this clue is complete
    const isComplete = isClueComplete(clue, state);
    if (isComplete) {
      li.classList.add("clue-item--complete");
    }

    // Check if this is the active clue
    if (
      state.activeDirection === clue.direction &&
      isInClue(clue, state.activeRow, state.activeCol)
    ) {
      li.classList.add("clue-item--active");
    }

    li.textContent = `${clue.clue_number}. ${clue.definition}`;

    li.addEventListener("click", () => onClueClick(clue));
    container.appendChild(li);
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
