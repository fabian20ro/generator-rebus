/**
 * Main application entry point.
 * Wires together: puzzle selector, grid, clues, hints, gamification.
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
  createGrid,
  updateGrid,
  focusCell,
  type GridState,
} from "./components/grid-renderer";
import { renderClues } from "./components/clue-panel";
import {
  handleCellClick,
  handleCellInput,
  handleKeyDown,
} from "./components/input-handler";
import { renderPuzzleList, renderDifficultyFilter } from "./components/puzzle-selector";
import {
  revealLetter,
  revealWord,
  checkPuzzle,
  isPuzzleComplete,
} from "./components/hint-system";
import { renderDefinitionBar } from "./components/definition-bar";
import { renderStatsPanel } from "./components/stats-panel";
import { showToast } from "./components/toast";
import {
  loadPlayerData,
  recordPuzzleCompletion,
  getPoints,
  isPuzzleAlreadySolved,
} from "./gamification/storage";
import {
  saveProgress,
  loadProgress,
  clearProgress,
} from "./gamification/progress-storage";
import {
  calculateScore,
  hintLetterCost,
  hintWordCost,
  CHECK_COST,
} from "./gamification/scoring";
import { evaluateBadges } from "./gamification/badges";
import { applySavedFontSize, initFontScaler } from "./components/font-scaler";
import { formatTime } from "./utils/format-time";
import { debounce } from "./utils/debounce";
import { UndoStack } from "./utils/undo-stack";
import { showTutorialIfNeeded } from "./components/tutorial";
import confetti from "canvas-confetti";

// --- DOM elements ---
const puzzleSelector = document.getElementById("puzzle-selector")!;
const puzzleList = document.getElementById("puzzle-list")!;
const puzzleView = document.getElementById("puzzle-view")!;
const puzzleTitle = document.getElementById("puzzle-title")!;
const gridContainer = document.getElementById("grid")!;
const progressCounter = document.getElementById("progress-counter")!;
const cluesH = document.getElementById("clues-horizontal")!;
const cluesV = document.getElementById("clues-vertical")!;
const btnCheck = document.getElementById("btn-check")!;
const btnHintLetter = document.getElementById("btn-hint-letter")!;
const btnHintWord = document.getElementById("btn-hint-word")!;
const btnBack = document.getElementById("btn-back")!;
const completionModal = document.getElementById("completion-modal")!;
const btnCloseModal = document.getElementById("btn-close-modal")!;
const definitionBar = document.getElementById("definition-bar")!;
const headerPoints = document.getElementById("header-points")!;
const statsPanel = document.getElementById("stats-panel")!;
const checkCostEl = document.getElementById("check-cost")!;
const hintLetterCostEl = document.getElementById("hint-letter-cost")!;
const hintWordCostEl = document.getElementById("hint-word-cost")!;
const completionDetails = document.getElementById("completion-details")!;
const navTabs = document.getElementById("nav-tabs")!;
const btnPencil = document.getElementById("btn-pencil")!;

// --- State ---
let gridState: GridState | null = null;
let currentPuzzleId: string | null = null;
let currentDifficulty = 1;
let currentGridSize = 10;
let puzzleStartTime = 0;
let hintsUsedCount = 0;
let pencilMode = false;

// --- Undo/Redo ---
const cellHistory = new UndoStack<(string | null)[][]>(50);

function deepCopyCells(cells: (string | null)[][]): (string | null)[][] {
  return cells.map((row) => [...row]);
}

// --- Progress persistence ---
const debouncedSaveProgress = debounce(() => saveCurrentProgress(), 500);

function saveCurrentProgress(): void {
  if (!currentPuzzleId || !gridState) return;
  if (isPuzzleAlreadySolved(currentPuzzleId)) return;
  const elapsed = Math.round((Date.now() - puzzleStartTime) / 1000);
  const cleanCells = gridState.cells.map((row) =>
    row.map((cell) => (cell === "!" ? null : cell))
  );
  saveProgress(currentPuzzleId, {
    cells: cleanCells,
    revealed: gridState.revealed,
    pencilCells: gridState.pencilCells,
    hintsUsed: hintsUsedCount,
    elapsedSeconds: elapsed,
    savedAt: new Date().toISOString(),
  });
}

// --- Points display ---
function updatePointsDisplay(): void {
  const pts = getPoints();
  headerPoints.textContent = `${pts} pts`;
}

function updateHintCosts(): void {
  checkCostEl.textContent = `${CHECK_COST} pts`;
  hintLetterCostEl.textContent = `${hintLetterCost(currentDifficulty)} pts`;
  hintWordCostEl.textContent = `${hintWordCost(currentDifficulty)} pts`;
}

// --- Navigation ---
function showTab(tab: "puzzles" | "stats"): void {
  saveCurrentProgress();
  navTabs.querySelectorAll(".nav-tab").forEach((btn) => {
    btn.classList.toggle(
      "nav-tab--active",
      (btn as HTMLElement).dataset.tab === tab
    );
  });

  puzzleView.classList.add("hidden");
  puzzleTitle.textContent = "";
  btnBack.classList.add("hidden");

  if (tab === "puzzles") {
    puzzleSelector.classList.remove("hidden");
    statsPanel.classList.add("hidden");
    showPuzzleList();
  } else {
    puzzleSelector.classList.add("hidden");
    statsPanel.classList.remove("hidden");
    renderStatsPanel(statsPanel);
  }
}

navTabs.addEventListener("click", (e) => {
  const btn = (e.target as HTMLElement).closest("[data-tab]") as HTMLElement;
  if (!btn) return;
  showTab(btn.dataset.tab as "puzzles" | "stats");
});

// --- Grid callback helpers (defined once, always reference current gridState) ---
function onGridCellClick(row: number, col: number): void {
  handleCellClick(gridState!, row, col);
  refresh();
  focusCell(gridContainer, row, col);
}

function onGridCellInput(row: number, col: number, value: string): void {
  cellHistory.push(deepCopyCells(gridState!.cells));
  handleCellInput(gridState!, row, col, value);
  if (value) {
    gridState!.pencilCells[row][col] = pencilMode;
  }
  refresh();
  focusCell(gridContainer, gridState!.activeRow, gridState!.activeCol);
  debouncedSaveProgress();
  if (isPuzzleComplete(gridState!)) {
    handleCompletion();
  }
}

function onGridKeyDown(row: number, col: number, e: KeyboardEvent): void {
  // Undo/Redo shortcuts
  if ((e.ctrlKey || e.metaKey) && e.key === "z") {
    e.preventDefault();
    const prev = cellHistory.undo();
    if (prev && gridState) {
      gridState.cells = prev;
      refresh();
    }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "y") {
    e.preventDefault();
    const next = cellHistory.redo();
    if (next && gridState) {
      gridState.cells = next;
      refresh();
    }
    return;
  }

  // Push undo state before letter keys, backspace, delete
  const isMutating =
    e.key === "Backspace" ||
    e.key === "Delete" ||
    (e.key.length === 1 && /^[A-Za-z]$/.test(e.key));
  if (isMutating && gridState) {
    cellHistory.push(deepCopyCells(gridState.cells));
  }

  const handled = handleKeyDown(gridState!, row, col, e);
  if (handled) {
    // Set pencil mode for letter key overwrites
    if (
      e.key.length === 1 &&
      /^[A-Za-z]$/.test(e.key) &&
      gridState
    ) {
      gridState.pencilCells[row][col] = pencilMode;
    }
    refresh();
    focusCell(gridContainer, gridState!.activeRow, gridState!.activeCol);
    debouncedSaveProgress();
  }
}

function onClueClick(clue: Clue): void {
  gridState!.activeRow = clue.start_row;
  gridState!.activeCol = clue.start_col;
  gridState!.activeDirection = clue.direction;
  refresh();
  focusCell(gridContainer, clue.start_row, clue.start_col);
}

/** Whether createGrid has been called for the current puzzle. */
let gridInitialised = false;

// --- Re-render the grid, clues, and definition bar ---
function refresh(): void {
  if (!gridState) return;

  if (!gridInitialised) {
    createGrid(
      gridContainer,
      gridState,
      onGridCellClick,
      onGridCellInput,
      onGridKeyDown
    );
    gridInitialised = true;
  } else {
    updateGrid(gridContainer, gridState);
  }

  renderClues(cluesH, cluesV, gridState, onClueClick);

  renderDefinitionBar(definitionBar, gridState);

  // Update progress counter
  updateProgressCounter(gridState);
}

function updateProgressCounter(state: GridState): void {
  let filled = 0;
  let total = 0;
  for (let r = 0; r < state.size; r++) {
    for (let c = 0; c < state.size; c++) {
      if (state.template[r][c]) {
        total++;
        const v = state.cells[r][c];
        if (v !== null && v !== "#") {
          filled++;
        }
      }
    }
  }
  progressCounter.textContent = `${filled}/${total}`;
}

// --- Completion handler ---
function handleCompletion(): void {
  if (!currentPuzzleId || !gridState) return;
  if (isPuzzleAlreadySolved(currentPuzzleId)) {
    completionDetails.innerHTML = "<p>Ai rezolvat deja acest rebus!</p>";
    completionModal.classList.remove("hidden");
    return;
  }

  const timeSeconds = Math.round((Date.now() - puzzleStartTime) / 1000);
  const score = calculateScore({
    difficulty: currentDifficulty,
    gridSize: currentGridSize,
    timeSeconds,
    hintsUsed: hintsUsedCount,
  });

  // Get badges before recording
  const badgesBefore = new Set(
    evaluateBadges(loadPlayerData()).map((b) => b.id)
  );

  const record = {
    puzzleId: currentPuzzleId,
    completedAt: new Date().toISOString(),
    timeSeconds,
    difficulty: currentDifficulty,
    gridSize: currentGridSize,
    hintsUsed: hintsUsedCount,
    pointsEarned: score.total,
    pointsSpent: 0,
  };

  recordPuzzleCompletion(record);
  clearProgress(currentPuzzleId);
  updatePointsDisplay();

  // Check for new badges
  const badgesAfter = evaluateBadges(loadPlayerData());
  const newBadges = badgesAfter.filter((b) => !badgesBefore.has(b.id));

  // Build completion modal content
  const timeStr = formatTime(timeSeconds);
  let html = `
    <div class="completion-score">
      <span class="completion-score__total">+${score.total}</span>
      <span class="completion-score__label">puncte</span>
    </div>
    <div class="completion-breakdown">
      <span><em>Baz\u0103</em> <strong>+${score.base}</strong></span>
      ${score.speedBonus > 0 ? `<span><em>Vitez\u0103</em> <strong>+${score.speedBonus}</strong></span>` : ""}
      ${score.noHintBonus > 0 ? `<span><em>F\u0103r\u0103 indicii</em> <strong>+${score.noHintBonus}</strong></span>` : ""}
    </div>
    <p>Timp: ${timeStr} | Indicii: ${hintsUsedCount}</p>
  `;

  if (newBadges.length > 0) {
    html += `<div class="completion-badges">`;
    for (const b of newBadges) {
      html += `<span class="completion-badge" title="${b.name}">${b.icon}</span>`;
    }
    html += `</div>`;
    html += `<p><strong>Insigne noi!</strong></p>`;
  }

  completionDetails.innerHTML = html;
  completionModal.classList.remove("hidden");

  // Confetti celebration
  confetti({ particleCount: 80, spread: 70, origin: { x: 0.3, y: 0.6 } });
  confetti({ particleCount: 80, spread: 70, origin: { x: 0.7, y: 0.6 } });
}


// --- Load a puzzle ---
async function loadPuzzle(id: string): Promise<void> {
  try {
    // Fetch puzzle and solution in parallel
    const [puzzleResult, solutionResult] = await Promise.allSettled([
      getPuzzle(id),
      getSolution(id),
    ]);

    if (puzzleResult.status === "rejected") {
      throw puzzleResult.reason;
    }
    const data: PuzzleDetail = puzzleResult.value;
    const template: boolean[][] = JSON.parse(data.puzzle.grid_template);

    gridState = createGridState(data.puzzle.grid_size, template, data.clues);
    gridInitialised = false; // force createGrid on next refresh
    cellHistory.clear();
    currentPuzzleId = id;
    currentDifficulty = data.puzzle.difficulty;
    currentGridSize = data.puzzle.grid_size;
    puzzleStartTime = Date.now();
    hintsUsedCount = 0;

    // Attach solution if available (hints require it)
    if (solutionResult.status === "fulfilled") {
      gridState.solution = JSON.parse(solutionResult.value.solution);
    }

    // Restore saved progress if available
    const saved = isPuzzleAlreadySolved(id) ? null : loadProgress(id);
    if (saved && saved.cells.length === gridState.size &&
        saved.cells.every((row) => row.length === gridState!.size)) {
      gridState.cells = saved.cells;
      if (saved.revealed && saved.revealed.length === gridState.size) {
        gridState.revealed = saved.revealed;
      }
      if (saved.pencilCells && saved.pencilCells.length === gridState.size) {
        gridState.pencilCells = saved.pencilCells;
      }
      hintsUsedCount = saved.hintsUsed;
      puzzleStartTime = Date.now() - saved.elapsedSeconds * 1000;
    }

    puzzleTitle.textContent = data.puzzle.title || "Rebus";
    puzzleSelector.classList.add("hidden");
    statsPanel.classList.add("hidden");
    puzzleView.classList.remove("hidden");
    navTabs.classList.add("hidden");
    btnBack.classList.remove("hidden");

    updateHintCosts();

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
    showToast("Nu s-a putut \u00eenc\u0103rca rebusul.", "warning");
  }
}

// --- Show puzzle list ---
async function showPuzzleList(): Promise<void> {
  gridState = null;
  currentPuzzleId = null;
  puzzleView.classList.add("hidden");
  statsPanel.classList.add("hidden");
  puzzleSelector.classList.remove("hidden");
  puzzleTitle.textContent = "";
  progressCounter.textContent = "";
  navTabs.classList.remove("hidden");
  btnBack.classList.add("hidden");

  const difficultyFilterEl = document.getElementById("difficulty-filter")!;

  try {
    const puzzles = await listPuzzles();
    renderDifficultyFilter(difficultyFilterEl, () => {
      renderPuzzleList(puzzleList, puzzles, loadPuzzle);
    });
    renderPuzzleList(puzzleList, puzzles, loadPuzzle);
  } catch (err) {
    console.error("Failed to load puzzle list:", err);
    puzzleList.innerHTML =
      "<p>Nu s-au putut \u00eenc\u0103rca rebus-urile. Verific\u0103 conexiunea.</p>";
  }
}

// --- Button handlers ---
btnCheck.addEventListener("click", () => {
  if (!gridState) return;
  const result = checkPuzzle(gridState);
  if (!result.success) {
    if (result.reason === "not_enough_points") {
      showToast(
        `Nu ai suficiente puncte! Ai nevoie de ${result.cost} pts.`,
        "warning"
      );
    }
    return;
  }
  updatePointsDisplay();
  refresh();
  if (result.wrong === 0 && result.empty === 0) {
    handleCompletion();
  } else {
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
      saveCurrentProgress();
    }, 2000);
  }
});

btnHintLetter.addEventListener("click", () => {
  if (!gridState) return;
  const result = revealLetter(gridState, currentDifficulty);
  if (result.success) {
    hintsUsedCount++;
    updatePointsDisplay();
    refresh();
    saveCurrentProgress();
    if (isPuzzleComplete(gridState)) {
      handleCompletion();
    }
  } else if (result.reason === "not_enough_points") {
    showToast(
      `Nu ai suficiente puncte! Ai nevoie de ${result.cost} pts.`,
      "warning"
    );
  }
});

btnHintWord.addEventListener("click", () => {
  if (!gridState) return;
  const result = revealWord(gridState, currentDifficulty);
  if (result.success) {
    hintsUsedCount++;
    updatePointsDisplay();
    refresh();
    saveCurrentProgress();
    if (isPuzzleComplete(gridState)) {
      handleCompletion();
    }
  } else if (result.reason === "not_enough_points") {
    showToast(
      `Nu ai suficiente puncte! Ai nevoie de ${result.cost} pts.`,
      "warning"
    );
  }
});

btnBack.addEventListener("click", () => {
  saveCurrentProgress();
  showTab("puzzles");
});

btnCloseModal.addEventListener("click", () => {
  completionModal.classList.add("hidden");
});

btnPencil.addEventListener("click", () => {
  pencilMode = !pencilMode;
  btnPencil.classList.toggle("btn-pencil--active", pencilMode);
});

// --- PWA: check for service worker updates every 3 minutes ---
import { registerSW } from "virtual:pwa-register";

registerSW({
  onRegisteredSW(swUrl, registration) {
    if (registration) {
      setInterval(() => {
        registration.update();
      }, 3 * 60 * 1000);
    }
  },
});

// --- Save progress on browser close ---
window.addEventListener("beforeunload", () => saveCurrentProgress());
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    saveCurrentProgress();
  }
});

// --- Init ---
applySavedFontSize();
initFontScaler(document.querySelector(".clues-container")!);
updatePointsDisplay();
showPuzzleList();
showTutorialIfNeeded();
