import type { GridState } from "../grid/grid-renderer";
import {
  buildPuzzleSessionViewModel,
  hydrateSolvedGridFromSolution,
  puzzleProgressText,
} from "./puzzle-session";

function grid(overrides: Partial<GridState> = {}): GridState {
  return {
    size: 2,
    template: [
      [true, false],
      [true, true],
    ],
    cells: [
      [null, "#"],
      ["A", null],
    ],
    revealed: [
      [false, false],
      [false, false],
    ],
    pencilCells: [
      [false, false],
      [false, false],
    ],
    solution: [
      ["C", null],
      ["A", "S"],
    ],
    isSolvedView: false,
    touchRemoteEnabled: true,
    clues: [],
    activeRow: 0,
    activeCol: 0,
    activeDirection: "H",
    ...overrides,
  };
}

describe("puzzle session view model", () => {
  test("reports filled cells over playable cells", () => {
    expect(puzzleProgressText(grid())).toBe("1/3");
  });

  test("hydrates a solved grid from the solution", () => {
    const state = grid();

    expect(hydrateSolvedGridFromSolution(state)).toBe(true);

    expect(state.cells).toEqual([
      ["C", "#"],
      ["A", "S"],
    ]);
    expect(state.isSolvedView).toBe(true);
  });

  test("hides touch remote and disables controls for solved view", () => {
    const view = buildPuzzleSessionViewModel(grid({ isSolvedView: true }), {
      currentPuzzleId: "p1",
      alreadySolved: true,
      touchRemoteEnabled: true,
    });

    expect(view.showTouchRemote).toBe(false);
    expect(view.controlsDisabled).toBe(true);
    expect(view.progressText).toBe("1/3");
  });
});
