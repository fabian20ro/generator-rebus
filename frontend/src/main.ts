/**
 * Main application entry point.
 * Wires together all components: puzzle selector, grid, clues, hints.
 */

import {
  listPuzzles,
  getPuzzle,
  getSolution,
  type PuzzleDetail,
  type Clue,
} from "./db/puzzle-repository";
import {
  createGridState,
  renderGrid,
  focusCell,
  type GridState,
} from "./components/grid-renderer";
import { renderClues } from "./components/clue-panel";
import {
  handleCellClick,
  handleCellInput,
  handleKeyDown,
} from "./components/input-handler";
import { renderPuzzleList } from "./components/puzzle-selector";
import {
  revealLetter,
  revealWord,
  checkPuzzle,
  isPuzzleComplete,
} from "./components/hint-system";

// DOM elements
const puzzleSelector = document.getElementById("puzzle-selector")!;
const puzzleList = document.getElementById("puzzle-list")!;
const puzzleView = document.getElementById("puzzle-view")!;
const puzzleTitle = document.getElementById("puzzle-title")!;
const gridContainer = document.getElementById("grid")!;
const cluesH = document.getElementById("clues-horizontal")!;
const cluesV = document.getElementById("clues-vertical")!;
const btnCheck = document.getElementById("btn-check")!;
const btnHintLetter = document.getElementById("btn-hint-letter")!;
const btnHintWord = document.getElementById("btn-hint-word")!;
const btnBack = document.getElementById("btn-back")!;
const completionModal = document.getElementById("completion-modal")!;
const btnCloseModal = document.getElementById("btn-close-modal")!;

let gridState: GridState | null = null;

// --- Re-render the grid and clues ---
function refresh(): void {
  if (!gridState) return;

  renderGrid(
    gridContainer,
    gridState,
    (row, col) => {
      handleCellClick(gridState!, row, col);
      refresh();
      focusCell(gridContainer, row, col);
    },
    (row, col, value) => {
      handleCellInput(gridState!, row, col, value);
      refresh();
      // Focus the next cell (cursor already advanced by handleCellInput)
      focusCell(gridContainer, gridState!.activeRow, gridState!.activeCol);
      // Check completion
      if (isPuzzleComplete(gridState!)) {
        completionModal.classList.remove("hidden");
      }
    },
    (row, col, e) => {
      const handled = handleKeyDown(gridState!, row, col, e);
      if (handled) {
        refresh();
        focusCell(gridContainer, gridState!.activeRow, gridState!.activeCol);
      }
    }
  );

  renderClues(cluesH, cluesV, gridState, (clue: Clue) => {
    gridState!.activeRow = clue.start_row;
    gridState!.activeCol = clue.start_col;
    gridState!.activeDirection = clue.direction;
    refresh();
    focusCell(gridContainer, clue.start_row, clue.start_col);
  });
}

// --- Load a puzzle ---
async function loadPuzzle(id: string): Promise<void> {
  try {
    const data: PuzzleDetail = await getPuzzle(id);
    const template: boolean[][] = JSON.parse(data.puzzle.grid_template);

    gridState = createGridState(data.puzzle.grid_size, template, data.clues);

    // Load solution in background
    getSolution(id)
      .then((sol) => {
        if (gridState) {
          gridState.solution = JSON.parse(sol.solution);
        }
      })
      .catch(() => {
        // Solution not available — hints won't work
      });

    puzzleTitle.textContent = data.puzzle.title || "Rebus";
    puzzleSelector.classList.add("hidden");
    puzzleView.classList.remove("hidden");

    // Focus the first letter cell
    for (let r = 0; r < gridState.size; r++) {
      for (let c = 0; c < gridState.size; c++) {
        if (template[r][c]) {
          gridState.activeRow = r;
          gridState.activeCol = c;
          refresh();
          focusCell(gridContainer, r, c);
          return;
        }
      }
    }

    refresh();
  } catch (err) {
    console.error("Failed to load puzzle:", err);
    alert("Nu s-a putut încărca rebusul.");
  }
}

// --- Show puzzle list ---
async function showPuzzleList(): Promise<void> {
  gridState = null;
  puzzleView.classList.add("hidden");
  puzzleSelector.classList.remove("hidden");
  puzzleTitle.textContent = "";

  try {
    const puzzles = await listPuzzles();
    renderPuzzleList(puzzleList, puzzles, loadPuzzle);
  } catch (err) {
    console.error("Failed to load puzzle list:", err);
    puzzleList.innerHTML = "<p>Nu s-au putut încărca rebus-urile. Verifică conexiunea.</p>";
  }
}

// --- Button handlers ---
btnCheck.addEventListener("click", () => {
  if (!gridState) return;
  const { correct, wrong, empty } = checkPuzzle(gridState);
  refresh();
  if (wrong === 0 && empty === 0) {
    completionModal.classList.remove("hidden");
  } else {
    // Wrong cells are temporarily marked with "!" — they'll show in red
    // Clear the "!" marks after a delay
    setTimeout(() => {
      if (!gridState) return;
      for (let r = 0; r < gridState.size; r++) {
        for (let c = 0; c < gridState.size; c++) {
          if (gridState.cells[r][c] === "!") {
            gridState.cells[r][c] = null;
          }
        }
      }
      refresh();
    }, 2000);
  }
});

btnHintLetter.addEventListener("click", () => {
  if (!gridState) return;
  revealLetter(gridState);
  refresh();
  if (isPuzzleComplete(gridState)) {
    completionModal.classList.remove("hidden");
  }
});

btnHintWord.addEventListener("click", () => {
  if (!gridState) return;
  revealWord(gridState);
  refresh();
  if (isPuzzleComplete(gridState)) {
    completionModal.classList.remove("hidden");
  }
});

btnBack.addEventListener("click", showPuzzleList);

btnCloseModal.addEventListener("click", () => {
  completionModal.classList.add("hidden");
});

// --- Init ---
showPuzzleList();
