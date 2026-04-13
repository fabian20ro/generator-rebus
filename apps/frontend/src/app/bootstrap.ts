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
  type PuzzleSummary,
} from "../shared/api/puzzles";
import {
  createGridState,
  createGrid,
  updateGrid,
  focusCell,
  type GridState,
} from "../features/puzzle-player/grid/grid-renderer";
import { renderClues } from "../features/puzzle-player/clues/clue-panel";
import {
  handleCellClick,
  handleCellInput,
  handleKeyDown,
  handleVirtualLetter,
  backspaceActiveCell,
  toggleDirection,
} from "../features/puzzle-player/grid/input-handler";
import {
  renderAvailableControls,
  renderPuzzleList,
} from "../features/puzzle-browser/puzzle-selector";
import {
  revealLetter,
  revealWord,
  checkPuzzle,
  isPuzzleComplete,
} from "../features/puzzle-player/hints/hint-system";
import { renderDefinitionBar } from "../features/puzzle-player/clues/definition-bar";
import { renderStatisticsPanel } from "../features/gamification/statistics-panel";
import { renderRewardsPanel } from "../features/gamification/rewards-panel";
import { showToast } from "../shared/ui/toast";
import {
  loadPlayerData,
  recordPuzzleCompletion,
  getPoints,
  isPuzzleAlreadySolved,
} from "../features/gamification/storage";
import {
  saveProgress,
  loadProgress,
  clearProgress,
  hasFilledCells,
  type PuzzleProgress,
} from "../features/gamification/progress-storage";
import {
  buildTabConfig,
  derivePuzzleState,
  filterAvailableBySize,
  type AppTab,
  type AvailableTabBrowseState,
  type DerivedPuzzleState,
} from "../features/gamification/puzzle-status";
import {
  calculateScore,
  hintLetterCost,
  hintWordCost,
  CHECK_COST,
} from "../features/gamification/scoring";
import { evaluateBadges } from "../features/gamification/badges";
import { applySavedFontSize, initFontScaler } from "../shared/ui/font-scaler";
import { formatTime } from "../shared/lib/format-time";
import { debounce } from "../shared/lib/debounce";
import { UndoStack } from "../shared/lib/undo-stack";
import { showTutorialIfNeeded } from "../features/onboarding/tutorial";
import { showPencilHelpIfNeeded } from "../features/onboarding/pencil-help";
import confetti from "canvas-confetti";

// --- DOM elements ---
const puzzleSelector = document.getElementById("puzzle-selector")!;
const selectorControls = document.getElementById("selector-controls")!;
const puzzleList = document.getElementById("puzzle-list")!;
const puzzleView = document.getElementById("puzzle-view")!;
const puzzleTitle = document.getElementById("puzzle-title")!;
const puzzleMeta = document.getElementById("puzzle-meta")!;
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
const btnPencilState = btnPencil.querySelector(".btn-pencil__state")!;
const touchRemote = document.getElementById("touch-remote")!;
const touchRemoteDirection = document.getElementById(
  "touch-remote-direction"
) as HTMLButtonElement;
const touchRemoteButtons = Array.from(
  touchRemote.querySelectorAll<HTMLButtonElement>("button")
);

// --- State ---
let gridState: GridState | null = null;
let allPuzzles: PuzzleSummary[] = [];
let currentPuzzleId: string | null = null;
let currentDifficulty = 1;
let currentGridSize = 10;
let puzzleStartTime = 0;
let hintsUsedCount = 0;
let checksUsedCount = 0;
let backspacesUsedCount = 0;
let pencilMode = false;
let puzzleListScrollY = 0;
let activeTab: AppTab = "available";
let availableBrowseState: AvailableTabBrowseState = {
  size: "all",
};
const touchRemoteEnabled = detectTouchRemoteEnabled();

document.documentElement.classList.toggle(
  "touch-remote-enabled",
  touchRemoteEnabled
);

function isSolvedGridView(): boolean {
  return !!gridState?.isSolvedView;
}

function detectTouchRemoteEnabled(): boolean {
  const coarsePointer = window.matchMedia?.("(any-pointer: coarse)").matches;
  return navigator.maxTouchPoints > 0 || !!coarsePointer;
}

function setPuzzleInteractionDisabled(disabled: boolean): void {
  btnCheck.toggleAttribute("disabled", disabled);
  btnHintLetter.toggleAttribute("disabled", disabled);
  btnHintWord.toggleAttribute("disabled", disabled);
  btnPencil.toggleAttribute("disabled", disabled);
  renderTouchRemote();
}

function hydrateSolvedGridFromSolution(state: GridState): boolean {
  if (!state.solution) return false;

  state.cells = state.solution.map((row, r) =>
    row.map((cell, c) => (state.template[r][c] ? cell : "#"))
  );
  state.revealed = state.template.map((row) => row.map((isLetter) => isLetter));
  state.pencilCells = state.template.map((row) => row.map(() => false));
  state.isSolvedView = true;
  return true;
}

function formatDateLabel(value?: string | null): string {
  if (!value) return "";
  const d = new Date(value);
  return (
    d.toLocaleDateString("ro-RO") + " " +
    d.toLocaleTimeString("ro-RO", { hour: "2-digit", minute: "2-digit" })
  );
}

function setPuzzleMeta(
  description?: string,
  createdAt?: string,
  repairedAt?: string | null,
  extraParts: string[] = []
): void {
  const parts: string[] = [];
  if (description) {
    parts.push(description);
  }
  parts.push(...extraParts.filter(Boolean));
  if (createdAt) {
    parts.push(`Creat: ${formatDateLabel(createdAt)}`);
  }
  if (repairedAt) {
    parts.push(`Ultima reparare: ${formatDateLabel(repairedAt)}`);
  }
  if (parts.length === 0) {
    puzzleMeta.textContent = "";
    puzzleMeta.classList.add("hidden");
    return;
  }
  puzzleMeta.textContent = parts.join(" | ");
  puzzleMeta.classList.remove("hidden");
}

function loadMeaningfulProgressById(puzzles: PuzzleSummary[]): Map<string, PuzzleProgress> {
  const progressById = new Map<string, PuzzleProgress>();

  for (const puzzle of puzzles) {
    if (isPuzzleAlreadySolved(puzzle.id)) {
      continue;
    }
    const progress = loadProgress(puzzle.id);
    if (!progress) {
      continue;
    }
    if (!hasFilledCells(progress)) {
      clearProgress(puzzle.id);
      continue;
    }
    progressById.set(puzzle.id, progress);
  }

  return progressById;
}

function getDerivedState(): DerivedPuzzleState {
  return derivePuzzleState(
    allPuzzles,
    loadPlayerData(),
    loadMeaningfulProgressById(allPuzzles),
  );
}

function getVisibleTabs(state: DerivedPuzzleState) {
  return buildTabConfig(state).filter((tab) => tab.visible);
}

function ensureActiveTab(state: DerivedPuzzleState): void {
  const tabs = getVisibleTabs(state);
  if (tabs.some((tab) => tab.id === activeTab)) {
    return;
  }
  activeTab = tabs[0]?.id ?? "available";
}

function renderNavTabs(state: DerivedPuzzleState): void {
  navTabs.innerHTML = "";
  navTabs.setAttribute("role", "tablist");
  const tabs = getVisibleTabs(state);

  for (const [index, tab] of tabs.entries()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "nav-tab";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(tab.id === activeTab));
    button.tabIndex = tab.id === activeTab ? 0 : -1;
    if (tab.id === activeTab) {
      button.classList.add("nav-tab--active");
    }
    button.title = tab.label;
    button.setAttribute("aria-label", tab.label);
    button.setAttribute("aria-pressed", String(tab.id === activeTab));

    const icon = document.createElement("span");
    icon.className = "nav-tab__icon";
    icon.textContent = tab.icon;
    button.appendChild(icon);

    if (tab.count) {
      const count = document.createElement("span");
      count.className = "nav-tab__count";
      count.textContent = String(tab.count);
      button.appendChild(count);
    }

    button.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") {
        return;
      }
      e.preventDefault();
      let nextIndex = index;
      if (e.key === "ArrowRight") {
        nextIndex = (index + 1) % tabs.length;
      } else if (e.key === "ArrowLeft") {
        nextIndex = (index - 1 + tabs.length) % tabs.length;
      } else if (e.key === "Home") {
        nextIndex = 0;
      } else if (e.key === "End") {
        nextIndex = tabs.length - 1;
      }
      const nextButton = navTabs.querySelectorAll<HTMLElement>(".nav-tab")[nextIndex];
      nextButton?.focus();
    });

    button.addEventListener("click", () => {
      showTab(tab.id);
    });

    navTabs.appendChild(button);
  }
}

function renderAvailableTab(state: DerivedPuzzleState): void {
  selectorControls.classList.remove("hidden");
  renderAvailableControls(selectorControls, availableBrowseState, (size) => {
    availableBrowseState = { size };
    renderCurrentTab();
  });

  const filtered = filterAvailableBySize(state.visibleAvailable, availableBrowseState.size);
  const hasOtherSizes = availableBrowseState.size !== "all";
  renderPuzzleList(
    puzzleList,
    filtered,
    loadPuzzle,
    "available",
    hasOtherSizes ? {
      emptyTitle: "Niciun rebus vizibil pentru dimensiunea asta",
      emptyBody: "Alege altă dimensiune sau revino la toate.",
      emptyActionLabel: "Toate",
      onEmptyAction: () => {
        availableBrowseState = { size: "all" };
        renderCurrentTab();
      },
    } : {
      emptyTitle: "Nu sunt rebusuri disponibile",
      emptyBody: "Revino puțin mai târziu.",
    },
  );
}

function renderCurrentTab(): void {
  const state = getDerivedState();
  ensureActiveTab(state);
  renderNavTabs(state);

  puzzleSelector.classList.remove("hidden");
  statsPanel.classList.add("hidden");
  selectorControls.innerHTML = "";

  switch (activeTab) {
    case "available":
      renderAvailableTab(state);
      return;
    case "in_progress":
      selectorControls.classList.add("hidden");
      renderPuzzleList(puzzleList, state.inProgress, loadPuzzle, "in_progress", {
        emptyTitle: "Nimic în curs",
        emptyBody: "Începe un rebus și completează măcar o literă.",
      });
      return;
    case "solved":
      selectorControls.classList.add("hidden");
      renderPuzzleList(puzzleList, state.solved, loadPuzzle, "solved", {
        emptyTitle: "Nimic rezolvat încă",
        emptyBody: "Primele rebusuri rezolvate apar aici.",
      });
      return;
    case "statistics":
      puzzleSelector.classList.add("hidden");
      statsPanel.classList.remove("hidden");
      renderStatisticsPanel(statsPanel, {
        inProgressCount: state.inProgress.length,
      });
      return;
    case "rewards":
      puzzleSelector.classList.add("hidden");
      statsPanel.classList.remove("hidden");
      renderRewardsPanel(statsPanel, {
        inProgressCount: state.inProgress.length,
      });
      return;
  }
}

function showTab(tab: AppTab): void {
  saveCurrentProgress();
  activeTab = tab;
  gridState = null;
  currentPuzzleId = null;

  puzzleView.classList.add("hidden");
  puzzleTitle.textContent = "";
  setPuzzleMeta();
  btnBack.classList.add("hidden");
  navTabs.classList.remove("hidden");
  renderTouchRemote();

  renderCurrentTab();
}

// --- Undo/Redo ---
const cellHistory = new UndoStack<(string | null)[][]>(50);

function deepCopyCells(cells: (string | null)[][]): (string | null)[][] {
  return cells.map((row) => [...row]);
}

// --- Progress persistence ---
const debouncedSaveProgress = debounce(() => saveCurrentProgress(), 500);

function saveCurrentProgress(): void {
  if (!currentPuzzleId || !gridState) return;
  if (gridState.isSolvedView) return;
  if (isPuzzleAlreadySolved(currentPuzzleId)) return;
  const elapsed = Math.round((Date.now() - puzzleStartTime) / 1000);
  const cleanCells = gridState.cells.map((row) =>
    row.map((cell) => (cell === "!" ? null : cell))
  );
  const progress = {
    cells: cleanCells,
    revealed: gridState.revealed,
    pencilCells: gridState.pencilCells,
    hintsUsed: hintsUsedCount,
    checksUsed: checksUsedCount,
    backspacesUsed: backspacesUsedCount,
    elapsedSeconds: elapsed,
    savedAt: new Date().toISOString(),
  };
  if (!hasFilledCells(progress)) {
    clearProgress(currentPuzzleId);
    return;
  }
  saveProgress(currentPuzzleId, progress);
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

function renderPencilButton(): void {
  btnPencil.classList.toggle("btn-pencil--active", pencilMode);
  btnPencil.setAttribute("aria-pressed", String(pencilMode));
  btnPencilState.textContent = pencilMode ? "Pornit" : "Oprit";
  const title = pencilMode
    ? "Literele noi vor fi marcate ca tentative"
    : "Literele noi vor fi introduse ca răspuns final";
  btnPencil.title = title;
  btnPencil.setAttribute(
    "aria-label",
    pencilMode
      ? "Creion pornit. Literele noi vor fi marcate ca tentative."
      : "Creion oprit. Literele noi vor fi introduse ca răspuns final."
  );
}

function focusActiveCell(): void {
  if (!gridState || gridState.activeRow < 0 || gridState.activeCol < 0) return;
  focusCell(gridContainer, gridState.activeRow, gridState.activeCol, {
    native: !touchRemoteEnabled,
  });
}

function renderTouchRemote(): void {
  const showRemote =
    touchRemoteEnabled && !!gridState && !gridState.isSolvedView;
  touchRemote.classList.toggle("hidden", !showRemote);

  const disabled = !showRemote;
  for (const button of touchRemoteButtons) {
    button.disabled = disabled;
  }

  if (!showRemote || !gridState) {
    touchRemoteDirection.classList.remove(
      "touch-remote__key--horizontal",
      "touch-remote__key--vertical"
    );
    return;
  }

  const isHorizontal = gridState.activeDirection === "H";
  touchRemoteDirection.classList.toggle(
    "touch-remote__key--horizontal",
    isHorizontal
  );
  touchRemoteDirection.classList.toggle(
    "touch-remote__key--vertical",
    !isHorizontal
  );
  touchRemoteDirection.setAttribute(
    "aria-label",
    isHorizontal
      ? "Direcție activă: orizontală. Apasă pentru verticală."
      : "Direcție activă: verticală. Apasă pentru orizontală."
  );
}

function performVirtualLetter(letter: string): void {
  if (!gridState || isSolvedGridView()) return;
  if (gridState.activeRow < 0 || gridState.activeCol < 0) return;

  const row = gridState.activeRow;
  const col = gridState.activeCol;
  cellHistory.push(deepCopyCells(gridState.cells));
  handleVirtualLetter(gridState, letter);
  gridState.pencilCells[row][col] = pencilMode;
  refresh();
  focusActiveCell();
  debouncedSaveProgress();

  if (isPuzzleComplete(gridState)) {
    handleCompletion();
  }
}

function performVirtualDelete(): void {
  if (!gridState || isSolvedGridView()) return;
  if (gridState.activeRow < 0 || gridState.activeCol < 0) return;

  cellHistory.push(deepCopyCells(gridState.cells));
  backspaceActiveCell(gridState);
  backspacesUsedCount++;
  refresh();
  focusActiveCell();
  debouncedSaveProgress();
}

function performDirectionToggle(): void {
  if (!gridState || isSolvedGridView()) return;
  if (gridState.activeRow < 0 || gridState.activeCol < 0) return;

  toggleDirection(gridState);
  refresh();
  focusActiveCell();
}

// --- Grid callback helpers (defined once, always reference current gridState) ---
function onGridCellClick(row: number, col: number): void {
  if (isSolvedGridView()) return;
  handleCellClick(gridState!, row, col);
  refresh();
  focusActiveCell();
}

function onGridCellInput(row: number, col: number, value: string): void {
  if (isSolvedGridView()) return;
  cellHistory.push(deepCopyCells(gridState!.cells));
  handleCellInput(gridState!, row, col, value);
  if (value) {
    gridState!.pencilCells[row][col] = pencilMode;
  }
  refresh();
  focusActiveCell();
  debouncedSaveProgress();
  if (isPuzzleComplete(gridState!)) {
    handleCompletion();
  }
}

function onGridKeyDown(row: number, col: number, e: KeyboardEvent): void {
  if (isSolvedGridView()) return;
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

  if ((e.key === "Backspace" || e.key === "Delete") && gridState) {
    backspacesUsedCount++;
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
    focusActiveCell();
    debouncedSaveProgress();
  }
}

function onClueClick(clue: Clue): void {
  gridState!.activeRow = clue.start_row;
  gridState!.activeCol = clue.start_col;
  gridState!.activeDirection = clue.direction;
  refresh();
  focusActiveCell();
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
  renderTouchRemote();

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
    checksUsed: checksUsedCount,
    backspacesUsed: backspacesUsedCount,
    pointsEarned: score.total,
    pointsSpent: 0,
  };

  recordPuzzleCompletion(record);
  clearProgress(currentPuzzleId);
  updatePointsDisplay();

  // Check for new badges
  const badgesAfter = evaluateBadges(loadPlayerData());
  const newBadges = badgesAfter.filter((b) => !badgesBefore.has(b.id));

  // Trigger REZOLVAT stamp
  const stampContainer = document.getElementById("stamp-container");
  if (stampContainer) {
    stampContainer.classList.remove("hidden");
    stampContainer.classList.add("animate-stamp");
    console.log("PUZZLE SOLVED: REZOLVAT!");
  }

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
    puzzleListScrollY = window.scrollY;
    const alreadySolved = isPuzzleAlreadySolved(id);

    // Fetch puzzle and solution in parallel
    const [puzzleResult, solutionResult] = await Promise.allSettled([
      getPuzzle(id),
      getSolution(id),
    ]);

    if (puzzleResult.status === "rejected") {
      throw puzzleResult.reason;
    }
    const data: PuzzleDetail = puzzleResult.value;
    const { puzzle, clues } = data;
    const template: boolean[][] = JSON.parse(puzzle.grid_template);

    gridState = createGridState(puzzle.grid_size, template, clues);
    gridState.touchRemoteEnabled = touchRemoteEnabled;
    gridInitialised = false; // force createGrid on next refresh
    cellHistory.clear();
    currentPuzzleId = puzzle.id;
    currentDifficulty = puzzle.difficulty;
    currentGridSize = puzzle.grid_size;
    puzzleStartTime = Date.now();
    hintsUsedCount = 0;
    checksUsedCount = 0;
    backspacesUsedCount = 0;


    // Attach solution if available (hints require it)
    if (solutionResult.status === "fulfilled") {
      gridState.solution = JSON.parse(solutionResult.value.solution);
    }

    setPuzzleInteractionDisabled(false);

    // Restore saved progress if available
    const savedRaw = alreadySolved ? null : loadProgress(id);
    const saved = savedRaw && hasFilledCells(savedRaw) ? savedRaw : null;
    if (savedRaw && !saved) {
      clearProgress(id);
    }
    if (alreadySolved) {
      if (hydrateSolvedGridFromSolution(gridState)) {
        pencilMode = false;
        renderPencilButton();
        setPuzzleInteractionDisabled(true);
      }
    } else if (saved && saved.cells.length === gridState.size &&
        saved.cells.every((row) => row.length === gridState!.size)) {
      gridState.cells = saved.cells;
      if (saved.revealed && saved.revealed.length === gridState.size) {
        gridState.revealed = saved.revealed;
      }
      if (saved.pencilCells && saved.pencilCells.length === gridState.size) {
        gridState.pencilCells = saved.pencilCells;
      }
      hintsUsedCount = saved.hintsUsed;
      checksUsedCount = saved.checksUsed ?? 0;
      backspacesUsedCount = saved.backspacesUsed ?? 0;
      puzzleStartTime = Date.now() - saved.elapsedSeconds * 1000;
    }

    const puzzleStatus = alreadySolved
      ? "Rezolvat"
      : saved
        ? "În curs"
        : "Disponibil";
    const extraMeta = [
      `${puzzle.grid_size}x${puzzle.grid_size}`,
      puzzleStatus,
    ];
    if (saved) {
      extraMeta.push("Continuare salvată");
    }

    puzzleTitle.textContent = puzzle.title || "Rebus";
    setPuzzleMeta(
      puzzle.description || puzzle.theme || "",
      puzzle.created_at,
      puzzle.repaired_at,
      extraMeta,
    );
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
          focusActiveCell();
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
  setPuzzleMeta();
  progressCounter.textContent = "";
  renderTouchRemote();
  navTabs.classList.remove("hidden");
  btnBack.classList.add("hidden");
  selectorControls.innerHTML = "";
  puzzleList.innerHTML = "<p class=\"loading\">Se încarcă...</p>";

  try {
    if (allPuzzles.length === 0) {
      allPuzzles = await listPuzzles();
    }
    renderCurrentTab();
    requestAnimationFrame(() => window.scrollTo({ top: puzzleListScrollY }));
  } catch (err) {
    console.error("Failed to load puzzle list:", err);
    selectorControls.innerHTML = "";
    puzzleList.innerHTML =
      "<p>Nu s-au putut \u00eenc\u0103rca rebus-urile. Verific\u0103 conexiunea.</p>";
  }
}

// --- Button handlers ---
btnCheck.addEventListener("click", () => {
  if (!gridState) return;
  if (gridState.isSolvedView) return;
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
  checksUsedCount++;
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
  if (gridState.isSolvedView) return;
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
  if (gridState.isSolvedView) return;
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
  showTab(activeTab);
});

btnCloseModal.addEventListener("click", () => {
  completionModal.classList.add("hidden");
});

btnPencil.addEventListener("click", async () => {
  if (isSolvedGridView()) return;
  if (!pencilMode) {
    const shouldEnable = await showPencilHelpIfNeeded();
    if (!shouldEnable) {
      pencilMode = false;
      renderPencilButton();
      return;
    }
    pencilMode = true;
    renderPencilButton();
    return;
  }

  pencilMode = false;
  renderPencilButton();
});

touchRemote.addEventListener("click", (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>(
    "[data-remote-action]"
  );
  if (!button || button.disabled) return;

  const action = button.dataset.remoteAction;
  if (action === "key") {
    performVirtualLetter(button.dataset.key || "");
    return;
  }
  if (action === "delete") {
    performVirtualDelete();
    return;
  }
  if (action === "direction") {
    performDirectionToggle();
  }
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
window.addEventListener("resize", () => {
  if (!gridState) return;
  refresh();
});

// --- Init ---
applySavedFontSize();
initFontScaler(document.querySelector(".clues-container")!);
renderPencilButton();
renderTouchRemote();
updatePointsDisplay();
showPuzzleList();
showTutorialIfNeeded();
