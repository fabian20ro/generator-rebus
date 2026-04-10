/**
 * Hint system: reveal letters/words, check answers.
 * Hints cost points from the gamification system.
 */

import type { GridState } from "../grid/grid-renderer";
import { findActiveClue } from "../grid/grid-renderer";
import { hintLetterCost, hintWordCost, CHECK_COST } from "../../gamification/scoring";
import { getPoints, spendPoints } from "../../gamification/storage";

export interface HintResult {
  success: boolean;
  reason?: "no_solution" | "no_selection" | "not_enough_points";
  cost?: number;
}

export function revealLetter(
  state: GridState,
  difficulty: number
): HintResult {
  if (!state.solution) return { success: false, reason: "no_solution" };
  if (state.activeRow < 0 || state.activeCol < 0)
    return { success: false, reason: "no_selection" };
  if (!state.template[state.activeRow][state.activeCol])
    return { success: false, reason: "no_selection" };

  const cost = hintLetterCost(difficulty);
  if (getPoints() < cost) {
    return { success: false, reason: "not_enough_points", cost };
  }

  const answer = state.solution[state.activeRow][state.activeCol];
  if (answer) {
    spendPoints(cost);
    state.cells[state.activeRow][state.activeCol] = answer;
    state.revealed[state.activeRow][state.activeCol] = true;
    return { success: true, cost };
  }
  return { success: false, reason: "no_selection" };
}

export function revealWord(
  state: GridState,
  difficulty: number
): HintResult {
  if (!state.solution) return { success: false, reason: "no_solution" };

  const clue = findActiveClue(state);
  if (!clue) return { success: false, reason: "no_selection" };

  const cost = hintWordCost(difficulty);
  if (getPoints() < cost) {
    return { success: false, reason: "not_enough_points", cost };
  }

  spendPoints(cost);
  for (let i = 0; i < clue.length; i++) {
    const r = clue.direction === "H" ? clue.start_row : clue.start_row + i;
    const c = clue.direction === "H" ? clue.start_col + i : clue.start_col;
    const answer = state.solution[r][c];
    if (answer) {
      state.cells[r][c] = answer;
      state.revealed[r][c] = true;
    }
  }
  return { success: true, cost };
}

export interface CheckResult {
  success: boolean;
  reason?: "no_solution" | "not_enough_points";
  cost?: number;
  correct: number;
  wrong: number;
  empty: number;
}

export function checkPuzzle(state: GridState): CheckResult {
  if (!state.solution)
    return { success: false, reason: "no_solution", correct: 0, wrong: 0, empty: 0 };

  if (getPoints() < CHECK_COST) {
    return { success: false, reason: "not_enough_points", cost: CHECK_COST, correct: 0, wrong: 0, empty: 0 };
  }

  spendPoints(CHECK_COST);

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
        state.cells[r][c] = "!";
      }
    }
  }

  return { success: true, cost: CHECK_COST, correct, wrong, empty };
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
