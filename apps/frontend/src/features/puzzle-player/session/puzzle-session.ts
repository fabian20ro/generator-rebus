import type { GridState } from "../grid/grid-renderer";
import { createGridState } from "../grid/grid-renderer";
import type { PuzzleDetail } from "../../../shared/types/puzzle";
import type { PuzzleProgress } from "../../gamification/progress-storage";

export interface PuzzleSessionState {
  gridState: GridState | null;
  currentPuzzleId: string | null;
  currentDifficulty: number;
  currentGridSize: number;
  puzzleStartTime: number;
  hintsUsedCount: number;
  checksUsedCount: number;
  backspacesUsedCount: number;
  pencilMode: boolean;
}

export interface PuzzleSessionLoadInput {
  detail: PuzzleDetail;
  solutionJson?: string;
  progress: PuzzleProgress | null;
  alreadySolved: boolean;
  touchRemoteEnabled: boolean;
  now: number;
}

export interface PuzzleSessionLoadResult {
  usedProgress: boolean;
  hydratedSolvedGrid: boolean;
}

export function createPuzzleSessionState(): PuzzleSessionState {
  return {
    gridState: null,
    currentPuzzleId: null,
    currentDifficulty: 1,
    currentGridSize: 10,
    puzzleStartTime: 0,
    hintsUsedCount: 0,
    checksUsedCount: 0,
    backspacesUsedCount: 0,
    pencilMode: false,
  };
}

export function resetPuzzleSession(state: PuzzleSessionState): void {
  state.gridState = null;
  state.currentPuzzleId = null;
  state.currentDifficulty = 1;
  state.currentGridSize = 10;
  state.puzzleStartTime = 0;
  state.hintsUsedCount = 0;
  state.checksUsedCount = 0;
  state.backspacesUsedCount = 0;
  state.pencilMode = false;
}

export function loadPuzzleSession(
  state: PuzzleSessionState,
  input: PuzzleSessionLoadInput
): PuzzleSessionLoadResult {
  const { puzzle, clues } = input.detail;
  const template: boolean[][] = JSON.parse(puzzle.grid_template);
  const gridState = createGridState(puzzle.grid_size, template, clues);
  gridState.touchRemoteEnabled = input.touchRemoteEnabled;

  if (input.solutionJson) {
    gridState.solution = JSON.parse(input.solutionJson);
  }

  state.gridState = gridState;
  state.currentPuzzleId = puzzle.id;
  state.currentDifficulty = puzzle.difficulty;
  state.currentGridSize = puzzle.grid_size;
  state.puzzleStartTime = input.now;
  state.hintsUsedCount = 0;
  state.checksUsedCount = 0;
  state.backspacesUsedCount = 0;

  if (input.alreadySolved) {
    const hydratedSolvedGrid = hydrateSolvedGridFromSolution(gridState);
    if (hydratedSolvedGrid) {
      state.pencilMode = false;
    }
    return { usedProgress: false, hydratedSolvedGrid };
  }

  const progress = input.progress;
  if (progress && progressMatchesGrid(progress, gridState)) {
    gridState.cells = progress.cells;
    if (progress.revealed && progress.revealed.length === gridState.size) {
      gridState.revealed = progress.revealed;
    }
    if (progress.pencilCells && progress.pencilCells.length === gridState.size) {
      gridState.pencilCells = progress.pencilCells;
    }
    state.hintsUsedCount = progress.hintsUsed;
    state.checksUsedCount = progress.checksUsed ?? 0;
    state.backspacesUsedCount = progress.backspacesUsed ?? 0;
    state.puzzleStartTime = input.now - progress.elapsedSeconds * 1000;
    return { usedProgress: true, hydratedSolvedGrid: false };
  }

  return { usedProgress: false, hydratedSolvedGrid: false };
}

export function buildPuzzleProgress(
  state: PuzzleSessionState,
  options: {
    now: number;
    alreadySolved: boolean;
  },
): PuzzleProgress | null {
  if (!state.currentPuzzleId || !state.gridState) return null;
  if (state.gridState.isSolvedView || options.alreadySolved) return null;

  const cleanCells = state.gridState.cells.map((row) =>
    row.map((cell) => (cell === "!" ? null : cell))
  );
  return {
    cells: cleanCells,
    revealed: state.gridState.revealed,
    pencilCells: state.gridState.pencilCells,
    hintsUsed: state.hintsUsedCount,
    checksUsed: state.checksUsedCount,
    backspacesUsed: state.backspacesUsedCount,
    elapsedSeconds: elapsedPuzzleSeconds(state, options.now),
    savedAt: new Date(options.now).toISOString(),
  };
}

export function elapsedPuzzleSeconds(state: PuzzleSessionState, now: number): number {
  return Math.round((now - state.puzzleStartTime) / 1000);
}

export function noteHintUsed(state: PuzzleSessionState): void {
  state.hintsUsedCount++;
}

export function noteCheckUsed(state: PuzzleSessionState): void {
  state.checksUsedCount++;
}

export function noteBackspaceUsed(state: PuzzleSessionState): void {
  state.backspacesUsedCount++;
}

function progressMatchesGrid(progress: PuzzleProgress, state: GridState): boolean {
  return progress.cells.length === state.size &&
    progress.cells.every((row) => row.length === state.size);
}

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
