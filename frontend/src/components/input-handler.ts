/**
 * Keyboard and navigation logic for the crossword grid.
 */

import type { GridState } from "./grid-renderer";

export function handleCellClick(
  state: GridState,
  row: number,
  col: number
): void {
  if (!state.template[row][col]) return;

  // Toggle direction if clicking the same cell
  if (row === state.activeRow && col === state.activeCol) {
    state.activeDirection = state.activeDirection === "H" ? "V" : "H";
  }

  state.activeRow = row;
  state.activeCol = col;
}

export function handleCellInput(
  state: GridState,
  row: number,
  col: number,
  value: string
): void {
  if (!state.template[row][col]) return;

  state.cells[row][col] = value || null;

  if (value) {
    // Advance to next cell in current direction
    advanceCursor(state);
  }
}

export function handleKeyDown(
  state: GridState,
  row: number,
  col: number,
  e: KeyboardEvent
): boolean {
  switch (e.key) {
    case "Backspace":
      if (!state.cells[row][col]) {
        // Move back if cell is empty
        retreatCursor(state);
      } else {
        state.cells[row][col] = null;
      }
      e.preventDefault();
      return true;

    case "ArrowLeft":
      moveCursor(state, 0, -1);
      e.preventDefault();
      return true;

    case "ArrowRight":
      moveCursor(state, 0, 1);
      e.preventDefault();
      return true;

    case "ArrowUp":
      moveCursor(state, -1, 0);
      e.preventDefault();
      return true;

    case "ArrowDown":
      moveCursor(state, 1, 0);
      e.preventDefault();
      return true;

    case "Tab":
      e.preventDefault();
      jumpToNextSlot(state, e.shiftKey);
      return true;

    default: {
      // Replace existing letter: when a single letter key is pressed on a
      // cell that already has content, the browser's maxLength=1 silently
      // blocks the input event. Intercept here so overwrite + advance works.
      if (
        e.key.length === 1 &&
        /^[A-Za-z]$/.test(e.key) &&
        state.cells[row][col]
      ) {
        state.cells[row][col] = e.key.toUpperCase();
        advanceCursor(state);
        e.preventDefault();
        return true;
      }
      return false;
    }
  }
}

function advanceCursor(state: GridState): void {
  const dr = state.activeDirection === "V" ? 1 : 0;
  const dc = state.activeDirection === "H" ? 1 : 0;
  moveCursor(state, dr, dc);
}

function retreatCursor(state: GridState): void {
  const dr = state.activeDirection === "V" ? -1 : 0;
  const dc = state.activeDirection === "H" ? -1 : 0;
  moveCursor(state, dr, dc);
}

function moveCursor(state: GridState, dr: number, dc: number): void {
  let nr = state.activeRow + dr;
  let nc = state.activeCol + dc;

  // Skip black squares
  while (
    nr >= 0 &&
    nr < state.size &&
    nc >= 0 &&
    nc < state.size &&
    !state.template[nr][nc]
  ) {
    nr += dr;
    nc += dc;
  }

  if (nr >= 0 && nr < state.size && nc >= 0 && nc < state.size) {
    state.activeRow = nr;
    state.activeCol = nc;
  }
}

function jumpToNextSlot(state: GridState, reverse: boolean): void {
  const dirClues = state.clues.filter(
    (c) => c.direction === state.activeDirection
  );

  if (dirClues.length === 0) return;

  // Find current clue index
  let currentIdx = dirClues.findIndex(
    (clue) =>
      (clue.direction === "H" &&
        state.activeRow === clue.start_row &&
        state.activeCol >= clue.start_col &&
        state.activeCol < clue.start_col + clue.length) ||
      (clue.direction === "V" &&
        state.activeCol === clue.start_col &&
        state.activeRow >= clue.start_row &&
        state.activeRow < clue.start_row + clue.length)
  );

  if (currentIdx === -1) currentIdx = 0;

  const nextIdx = reverse
    ? (currentIdx - 1 + dirClues.length) % dirClues.length
    : (currentIdx + 1) % dirClues.length;

  const nextClue = dirClues[nextIdx];
  state.activeRow = nextClue.start_row;
  state.activeCol = nextClue.start_col;
}
