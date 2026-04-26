import type { GridState } from "../grid/grid-renderer";

export interface PuzzleSessionViewModel {
  progressText: string;
  showTouchRemote: boolean;
  controlsDisabled: boolean;
  activeDirection: "H" | "V";
}

export interface PuzzleSessionViewInput {
  currentPuzzleId: string | null;
  alreadySolved: boolean;
  touchRemoteEnabled: boolean;
}

export function hydrateSolvedGridFromSolution(state: GridState): boolean {
  if (!state.solution) return false;

  state.cells = state.solution.map((row, r) =>
    row.map((cell, c) => (state.template[r][c] ? cell : "#"))
  );
  state.revealed = state.template.map((row) => row.map((isLetter) => isLetter));
  state.pencilCells = state.template.map((row) => row.map(() => false));
  state.isSolvedView = true;
  return true;
}

export function puzzleProgressText(state: GridState): string {
  let filled = 0;
  let total = 0;
  for (let r = 0; r < state.size; r++) {
    for (let c = 0; c < state.size; c++) {
      if (!state.template[r][c]) continue;
      total++;
      const value = state.cells[r][c];
      if (value !== null && value !== "#") {
        filled++;
      }
    }
  }
  return `${filled}/${total}`;
}

export function buildPuzzleSessionViewModel(
  state: GridState | null,
  input: PuzzleSessionViewInput
): PuzzleSessionViewModel {
  const hasPlayablePuzzle = !!state && !!input.currentPuzzleId;
  const controlsDisabled = !hasPlayablePuzzle || !!state?.isSolvedView || input.alreadySolved;
  return {
    progressText: state ? puzzleProgressText(state) : "",
    showTouchRemote: input.touchRemoteEnabled && !!state && !state.isSolvedView,
    controlsDisabled,
    activeDirection: state?.activeDirection ?? "H",
  };
}
