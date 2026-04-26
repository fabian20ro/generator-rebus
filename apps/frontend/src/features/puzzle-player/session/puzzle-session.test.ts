import type { GridState } from "../grid/grid-renderer";
import {
  buildPuzzleProgress,
  buildPuzzleSessionViewModel,
  createPuzzleSessionState,
  hydrateSolvedGridFromSolution,
  loadPuzzleSession,
  noteBackspaceUsed,
  noteCheckUsed,
  noteHintUsed,
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

describe("puzzle session workflow", () => {
  test("loads saved progress and restores counters", () => {
    const session = createPuzzleSessionState();

    const result = loadPuzzleSession(session, {
      detail: {
        puzzle: {
          id: "p1",
          title: "Rebus",
          theme: "",
          grid_size: 2,
          grid_template: JSON.stringify([
            [true, false],
            [true, true],
          ]),
          difficulty: 3,
          created_at: "2026-04-26T00:00:00Z",
        },
        clues: [],
      },
      solutionJson: JSON.stringify([
        ["C", null],
        ["A", "S"],
      ]),
      progress: {
        cells: [
          ["C", "#"],
          [null, null],
        ],
        revealed: [
          [true, false],
          [false, false],
        ],
        pencilCells: [
          [false, false],
          [true, false],
        ],
        hintsUsed: 2,
        checksUsed: 1,
        backspacesUsed: 3,
        elapsedSeconds: 60,
        savedAt: "2026-04-26T00:01:00Z",
      },
      alreadySolved: false,
      touchRemoteEnabled: true,
      now: 120_000,
    });

    expect(result.usedProgress).toBe(true);
    expect(session.currentPuzzleId).toBe("p1");
    expect(session.currentDifficulty).toBe(3);
    expect(session.puzzleStartTime).toBe(60_000);
    expect(session.gridState?.cells[0][0]).toBe("C");
    expect(session.hintsUsedCount).toBe(2);
    expect(session.checksUsedCount).toBe(1);
    expect(session.backspacesUsedCount).toBe(3);
  });

  test("builds clean progress from session state", () => {
    const session = createPuzzleSessionState();
    session.currentPuzzleId = "p1";
    session.puzzleStartTime = 1_000;
    session.gridState = grid({
      cells: [
        ["!", "#"],
        ["A", null],
      ],
    });

    noteHintUsed(session);
    noteCheckUsed(session);
    noteBackspaceUsed(session);

    expect(buildPuzzleProgress(session, { now: 6_000, alreadySolved: false })).toMatchObject({
      cells: [
        [null, "#"],
        ["A", null],
      ],
      hintsUsed: 1,
      checksUsed: 1,
      backspacesUsed: 1,
      elapsedSeconds: 5,
    });
  });
});
