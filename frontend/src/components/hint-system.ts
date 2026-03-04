/**
 * Hint system: reveal letters, check answers.
 */

import type { GridState } from "./grid-renderer";
import { findActiveClue } from "./grid-renderer";

export function revealLetter(state: GridState): boolean {
  if (!state.solution) return false;
  if (state.activeRow < 0 || state.activeCol < 0) return false;
  if (!state.template[state.activeRow][state.activeCol]) return false;

  const answer = state.solution[state.activeRow][state.activeCol];
  if (answer) {
    state.cells[state.activeRow][state.activeCol] = answer;
    return true;
  }
  return false;
}

export function revealWord(state: GridState): boolean {
  if (!state.solution) return false;

  const clue = findActiveClue(state);
  if (!clue) return false;

  for (let i = 0; i < clue.length; i++) {
    const r = clue.direction === "H" ? clue.start_row : clue.start_row + i;
    const c = clue.direction === "H" ? clue.start_col + i : clue.start_col;
    const answer = state.solution[r][c];
    if (answer) {
      state.cells[r][c] = answer;
    }
  }
  return true;
}

export function checkPuzzle(state: GridState): {
  correct: number;
  wrong: number;
  empty: number;
} {
  if (!state.solution) return { correct: 0, wrong: 0, empty: 0 };

  let correct = 0;
  let wrong = 0;
  let empty = 0;

  for (let r = 0; r < state.size; r++) {
    for (let c = 0; c < state.size; c++) {
      if (!state.template[r][c]) continue;

      const userVal = state.cells[r][c];
      const answer = state.solution[r][c];

      if (!userVal) {
        empty++;
      } else if (userVal === answer) {
        correct++;
      } else {
        wrong++;
        // Mark as wrong temporarily (will be cleared on next input)
        state.cells[r][c] = "!";
      }
    }
  }

  return { correct, wrong, empty };
}

export function isPuzzleComplete(state: GridState): boolean {
  if (!state.solution) return false;

  for (let r = 0; r < state.size; r++) {
    for (let c = 0; c < state.size; c++) {
      if (!state.template[r][c]) continue;
      if (state.cells[r][c] !== state.solution[r][c]) {
        return false;
      }
    }
  }
  return true;
}
