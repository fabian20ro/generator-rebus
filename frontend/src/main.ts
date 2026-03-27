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
import {
  getPuzzleSizeGroup,
  renderBrowseControls,
  renderChallengeHighlight,
  renderContinueSection,
  renderPuzzleList,
  renderSelectorSummary,
  type PuzzleBrowseItem,
  type PuzzleBrowseState,
} from "./components/puzzle-selector";
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
  hasFilledCells,
} from "./gamification/progress-storage";
import { deriveChallenges, pickMenuChallenge } from "./gamification/challenges";
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
import { showPencilHelpIfNeeded } from "./components/pencil-help";
import confetti from "canvas-confetti";

// --- DOM elements ---
const puzzleSelector = document.getElementById("puzzle-selector")!;
const selectorContinue = document.getElementById("selector-continue")!;
const selectorChallenge = document.getElementById("selector-challenge")!;
const selectorControls = document.getElementById("selector-controls")!;
const selectorSummary = document.getElementById("selector-summary")!;
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

// --- State ---
let gridState: GridState | null = null;
let allPuzzles: PuzzleSummary[] = [];
let currentPuzzleId: string | null = null;
let currentDifficulty = 1;
let currentGridSize = 10;
let puzzleStartTime = 0;
let hintsUsedCount = 0;
let checksUsedCount = 0;
let pencilMode = false;
let puzzleListScrollY = 0;
let browseState: PuzzleBrowseState = {
  status: "all",
  hideCompleted: false,
  sizeGroup: "all",
  sort: "recent",
};

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

function resetBrowseState(): void {
  browseState = {
    status: "all",
    hideCompleted: false,
    sizeGroup: "all",
    sort: "recent",
  };
}

function buildBrowseItems(puzzles: PuzzleSummary[]): PuzzleBrowseItem[] {
  return puzzles.map((puzzle) => {
    const solved = isPuzzleAlreadySolved(puzzle.id);
    const saved = solved ? null : loadProgress(puzzle.id);
    const progress = saved && hasFilledCells(saved) ? saved : null;
    return {
      ...puzzle,
      localStatus: solved ? "solved" : progress ? "in_progress" : "unplayed",
      sizeGroup: getPuzzleSizeGroup(puzzle.grid_size),
      savedAt: progress?.savedAt ?? null,
    };
  });
}

function getSortTimestamp(puzzle: PuzzleBrowseItem): number {
  const raw = puzzle.savedAt || puzzle.repaired_at || puzzle.created_at || "";
  const timestamp = Date.parse(raw);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function applyBrowseState(items: PuzzleBrowseItem[]): PuzzleBrowseItem[] {
  return [...items]
    .filter((item) => {
      if (browseState.hideCompleted && item.localStatus === "solved") {
        return false;
      }
      if (browseState.sizeGroup !== "all" && item.sizeGroup !== browseState.sizeGroup) {
        return false;
      }
      switch (browseState.status) {
        case "in_progress":
          return item.localStatus === "in_progress";
        case "unsolved":
          return item.localStatus !== "solved";
        case "solved":
          return item.localStatus === "solved";
        default:
          return true;
      }
    })
    .sort((a, b) => {
      switch (browseState.sort) {
        case "size_asc":
          return a.grid_size - b.grid_size || getSortTimestamp(b) - getSortTimestamp(a);
        case "size_desc":
          return b.grid_size - a.grid_size || getSortTimestamp(b) - getSortTimestamp(a);
        case "title":
          return (a.title || "Rebus").localeCompare(b.title || "Rebus", "ro");
        default:
          return getSortTimestamp(b) - getSortTimestamp(a);
      }
    });
}

function buildBrowseSummary(
  totalCount: number,
  visibleCount: number
): { totalCount: number; visibleCount: number; activeLabels: string[] } {
  const labels: string[] = [];
  if (browseState.status === "in_progress") labels.push("Doar în curs");
  if (browseState.status === "unsolved") labels.push("Doar nerezolvate");
  if (browseState.status === "solved") labels.push("Doar rezolvate");
  if (browseState.sizeGroup === "small") labels.push("Mic 7-9");
  if (browseState.sizeGroup === "medium") labels.push("Mediu 10-12");
  if (browseState.sizeGroup === "large") labels.push("Mare 13-15");
  if (browseState.hideCompleted) labels.push("Ascunde rezolvate");
  if (browseState.sort === "size_asc") labels.push("Mărime crescător");
  if (browseState.sort === "size_desc") labels.push("Mărime descrescător");
  if (browseState.sort === "title") labels.push("A-Z");
  return { totalCount, visibleCount, activeLabels: labels };
}

function renderProgressPanel(): void {
  const items = buildBrowseItems(allPuzzles);
  const inProgressCount = items.filter((item) => item.localStatus === "in_progress").length;
  renderStatsPanel(statsPanel, {
    inProgressCount,
    challenges: deriveChallenges(loadPlayerData(), inProgressCount),
  });
}

function renderPuzzleSelector(): void {
  const items = buildBrowseItems(allPuzzles);
  const allInProgress = items.filter((item) => item.localStatus === "in_progress");
  const inProgress = allInProgress
    .filter((item) => browseState.sizeGroup === "all" || item.sizeGroup === browseState.sizeGroup)
    .sort((a, b) => getSortTimestamp(b) - getSortTimestamp(a));
  const visible = applyBrowseState(items);
  const challenges = deriveChallenges(loadPlayerData(), allInProgress.length);

  renderBrowseControls(
    selectorControls,
    browseState,
    (patch) => {
      browseState = { ...browseState, ...patch };
      renderPuzzleSelector();
    },
    () => {
      resetBrowseState();
      renderPuzzleSelector();
    }
  );
  renderContinueSection(
    selectorContinue,
    browseState.status === "solved" ? [] : inProgress.slice(0, 3),
    loadPuzzle
  );
  renderChallengeHighlight(selectorChallenge, pickMenuChallenge(challenges));
  renderSelectorSummary(selectorSummary, buildBrowseSummary(items.length, visible.length));
  renderPuzzleList(
    puzzleList,
    visible,
    loadPuzzle,
    () => {
      resetBrowseState();
      renderPuzzleSelector();
    },
    items.length
  );
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
  btnPencil.title = pencilMode
    ? "Literele noi vor fi marcate ca tentative"
    : "Literele noi vor fi introduse ca răspuns final";
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
  setPuzzleMeta();
  btnBack.classList.add("hidden");

  if (tab === "puzzles") {
    puzzleSelector.classList.remove("hidden");
    statsPanel.classList.add("hidden");
    showPuzzleList();
  } else {
    puzzleSelector.classList.add("hidden");
    statsPanel.classList.remove("hidden");
    renderProgressPanel();
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
    checksUsed: checksUsedCount,
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
    puzzleListScrollY = window.scrollY;

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
    checksUsedCount = 0;

    // Attach solution if available (hints require it)
    if (solutionResult.status === "fulfilled") {
      gridState.solution = JSON.parse(solutionResult.value.solution);
    }

    // Restore saved progress if available
    const savedRaw = isPuzzleAlreadySolved(id) ? null : loadProgress(id);
    const saved = savedRaw && hasFilledCells(savedRaw) ? savedRaw : null;
    if (savedRaw && !saved) {
      clearProgress(id);
    }
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
      checksUsedCount = saved.checksUsed ?? 0;
      puzzleStartTime = Date.now() - saved.elapsedSeconds * 1000;
    }

    const puzzleStatus = isPuzzleAlreadySolved(id)
      ? "Rezolvat"
      : saved
        ? "În curs"
        : "Nou";
    const extraMeta = [
      `${data.puzzle.grid_size}x${data.puzzle.grid_size}`,
      puzzleStatus,
    ];
    if (saved) {
      extraMeta.push("Continuare salvată");
    }

    puzzleTitle.textContent = data.puzzle.title || "Rebus";
    setPuzzleMeta(
      data.puzzle.description || data.puzzle.theme || "",
      data.puzzle.created_at,
      data.puzzle.repaired_at,
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
  setPuzzleMeta();
  progressCounter.textContent = "";
  navTabs.classList.remove("hidden");
  btnBack.classList.add("hidden");
  selectorContinue.classList.add("hidden");
  selectorChallenge.classList.add("hidden");
  selectorSummary.innerHTML = "";
  selectorControls.innerHTML = "";
  puzzleList.innerHTML = "<p class=\"loading\">Se încarcă...</p>";

  try {
    if (allPuzzles.length === 0) {
      allPuzzles = await listPuzzles();
    }
    renderPuzzleSelector();
    requestAnimationFrame(() => window.scrollTo({ top: puzzleListScrollY }));
  } catch (err) {
    console.error("Failed to load puzzle list:", err);
    selectorContinue.classList.add("hidden");
    selectorChallenge.classList.add("hidden");
    selectorSummary.innerHTML = "";
    selectorControls.innerHTML = "";
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

btnPencil.addEventListener("click", async () => {
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
renderPencilButton();
updatePointsDisplay();
showPuzzleList();
showTutorialIfNeeded();
