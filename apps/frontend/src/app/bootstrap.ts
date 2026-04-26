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
  createGrid,
  updateGrid,
  focusCell,
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
  resetCompletionOverlay,
  showCompletionCelebration,
} from "../features/gamification/completion-overlay";
import {
  revealLetter,
  revealWord,
  checkPuzzle,
  isPuzzleComplete,
} from "../features/puzzle-player/hints/hint-system";
import {
  buildPuzzleSessionViewModel,
  buildPuzzleProgress,
  createPuzzleSessionState,
  elapsedPuzzleSeconds,
  loadPuzzleSession,
  noteBackspaceUsed,
  noteCheckUsed,
  noteHintUsed,
  resetPuzzleSession,
} from "../features/puzzle-player/session/puzzle-session";
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
const stampContainer = document.getElementById("stamp-container")!;
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
const completionOverlay = {
  completionModal,
  stampContainer,
  completionDetails,
};

// --- State ---
const session = createPuzzleSessionState();
let allPuzzles: PuzzleSummary[] = [];
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
  return !!session.gridState?.isSolvedView;
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
  resetCompletionOverlay(completionOverlay);
  activeTab = tab;
  resetPuzzleSession(session);

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
  if (!session.currentPuzzleId) return;
  const progress = buildPuzzleProgress(session, {
    now: Date.now(),
    alreadySolved: isPuzzleAlreadySolved(session.currentPuzzleId),
  });
  if (!progress) return;
  if (!hasFilledCells(progress)) {
    clearProgress(session.currentPuzzleId);
    return;
  }
  saveProgress(session.currentPuzzleId, progress);
}

// --- Points display ---
function updatePointsDisplay(): void {
  const pts = getPoints();
  headerPoints.textContent = `${pts} pts`;
}

function updateHintCosts(): void {
  checkCostEl.textContent = `${CHECK_COST} pts`;
  hintLetterCostEl.textContent = `${hintLetterCost(session.currentDifficulty)} pts`;
  hintWordCostEl.textContent = `${hintWordCost(session.currentDifficulty)} pts`;
}

function renderPencilButton(): void {
  btnPencil.classList.toggle("btn-pencil--active", session.pencilMode);
  btnPencil.setAttribute("aria-pressed", String(session.pencilMode));
  btnPencilState.textContent = session.pencilMode ? "Pornit" : "Oprit";
  const title = session.pencilMode
    ? "Literele noi vor fi marcate ca tentative"
    : "Literele noi vor fi introduse ca răspuns final";
  btnPencil.title = title;
  btnPencil.setAttribute(
    "aria-label",
    session.pencilMode
      ? "Creion pornit. Literele noi vor fi marcate ca tentative."
      : "Creion oprit. Literele noi vor fi introduse ca răspuns final."
  );
}

function focusActiveCell(): void {
  if (!session.gridState || session.gridState.activeRow < 0 || session.gridState.activeCol < 0) return;
  focusCell(gridContainer, session.gridState.activeRow, session.gridState.activeCol, {
    native: !touchRemoteEnabled,
  });
}

function renderTouchRemote(): void {
  const view = buildPuzzleSessionViewModel(session.gridState, {
    currentPuzzleId: session.currentPuzzleId,
    alreadySolved: session.currentPuzzleId ? isPuzzleAlreadySolved(session.currentPuzzleId) : false,
    touchRemoteEnabled,
  });
  touchRemote.classList.toggle("hidden", !view.showTouchRemote);

  const disabled = !view.showTouchRemote;
  for (const button of touchRemoteButtons) {
    button.disabled = disabled;
  }

  if (!view.showTouchRemote || !session.gridState) {
    touchRemoteDirection.classList.remove(
      "touch-remote__key--horizontal",
      "touch-remote__key--vertical"
    );
    return;
  }

  const isHorizontal = view.activeDirection === "H";
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
  if (!session.gridState || isSolvedGridView()) return;
  if (session.gridState.activeRow < 0 || session.gridState.activeCol < 0) return;

  const row = session.gridState.activeRow;
  const col = session.gridState.activeCol;
  cellHistory.push(deepCopyCells(session.gridState.cells));
  handleVirtualLetter(session.gridState, letter);
  session.gridState.pencilCells[row][col] = session.pencilMode;
  refresh();
  focusActiveCell();
  debouncedSaveProgress();

  if (isPuzzleComplete(session.gridState)) {
    handleCompletion();
  }
}

function performVirtualDelete(): void {
  if (!session.gridState || isSolvedGridView()) return;
  if (session.gridState.activeRow < 0 || session.gridState.activeCol < 0) return;

  cellHistory.push(deepCopyCells(session.gridState.cells));
  backspaceActiveCell(session.gridState);
  noteBackspaceUsed(session);
  refresh();
  focusActiveCell();
  debouncedSaveProgress();
}

function performDirectionToggle(): void {
  if (!session.gridState || isSolvedGridView()) return;
  if (session.gridState.activeRow < 0 || session.gridState.activeCol < 0) return;

  toggleDirection(session.gridState);
  refresh();
  focusActiveCell();
}

// --- Grid callback helpers (defined once, always reference current session.gridState) ---
function onGridCellClick(row: number, col: number): void {
  if (isSolvedGridView()) return;
  handleCellClick(session.gridState!, row, col);
  refresh();
  focusActiveCell();
}

function onGridCellInput(row: number, col: number, value: string): void {
  if (isSolvedGridView()) return;
  cellHistory.push(deepCopyCells(session.gridState!.cells));
  handleCellInput(session.gridState!, row, col, value);
  if (value) {
    session.gridState!.pencilCells[row][col] = session.pencilMode;
  }
  refresh();
  focusActiveCell();
  debouncedSaveProgress();
  if (isPuzzleComplete(session.gridState!)) {
    handleCompletion();
  }
}

function onGridKeyDown(row: number, col: number, e: KeyboardEvent): void {
  if (isSolvedGridView()) return;
  // Undo/Redo shortcuts
  if ((e.ctrlKey || e.metaKey) && e.key === "z") {
    e.preventDefault();
    const prev = cellHistory.undo();
    if (prev && session.gridState) {
      session.gridState.cells = prev;
      refresh();
    }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "y") {
    e.preventDefault();
    const next = cellHistory.redo();
    if (next && session.gridState) {
      session.gridState.cells = next;
      refresh();
    }
    return;
  }

  // Push undo state before letter keys, backspace, delete
  const isMutating =
    e.key === "Backspace" ||
    e.key === "Delete" ||
    (e.key.length === 1 && /^[A-Za-z]$/.test(e.key));
  if (isMutating && session.gridState) {
    cellHistory.push(deepCopyCells(session.gridState.cells));
  }

  if ((e.key === "Backspace" || e.key === "Delete") && session.gridState) {
    noteBackspaceUsed(session);
  }

  const handled = handleKeyDown(session.gridState!, row, col, e);
  if (handled) {
    // Set pencil mode for letter key overwrites
    if (
      e.key.length === 1 &&
      /^[A-Za-z]$/.test(e.key) &&
      session.gridState
    ) {
      session.gridState.pencilCells[row][col] = session.pencilMode;
    }
    refresh();
    focusActiveCell();
    debouncedSaveProgress();
  }
}

function onClueClick(clue: Clue): void {
  session.gridState!.activeRow = clue.start_row;
  session.gridState!.activeCol = clue.start_col;
  session.gridState!.activeDirection = clue.direction;
  refresh();
  focusActiveCell();
}

/** Whether createGrid has been called for the current puzzle. */
let gridInitialised = false;

// --- Re-render the grid, clues, and definition bar ---
function refresh(): void {
  if (!session.gridState) return;

  if (!gridInitialised) {
    createGrid(
      gridContainer,
      session.gridState,
      onGridCellClick,
      onGridCellInput,
      onGridKeyDown
    );
    gridInitialised = true;
  } else {
    updateGrid(gridContainer, session.gridState);
  }

  renderClues(cluesH, cluesV, session.gridState, onClueClick);

  renderDefinitionBar(definitionBar, session.gridState);
  renderTouchRemote();

  const view = buildPuzzleSessionViewModel(session.gridState, {
    currentPuzzleId: session.currentPuzzleId,
    alreadySolved: session.currentPuzzleId ? isPuzzleAlreadySolved(session.currentPuzzleId) : false,
    touchRemoteEnabled,
  });
  progressCounter.textContent = view.progressText;
}

// --- Completion handler ---
function handleCompletion(): void {
  if (!session.currentPuzzleId || !session.gridState) return;
  if (isPuzzleAlreadySolved(session.currentPuzzleId)) {
    resetCompletionOverlay(completionOverlay);
    completionDetails.innerHTML = "<p>Ai rezolvat deja acest rebus!</p>";
    completionModal.classList.remove("hidden");
    return;
  }

  const timeSeconds = elapsedPuzzleSeconds(session, Date.now());
  const score = calculateScore({
    difficulty: session.currentDifficulty,
    gridSize: session.currentGridSize,
    timeSeconds,
    hintsUsed: session.hintsUsedCount,
  });

  // Get badges before recording
  const badgesBefore = new Set(
    evaluateBadges(loadPlayerData()).map((b) => b.id)
  );

  const record = {
    puzzleId: session.currentPuzzleId,
    completedAt: new Date().toISOString(),
    timeSeconds,
    difficulty: session.currentDifficulty,
    gridSize: session.currentGridSize,
    hintsUsed: session.hintsUsedCount,
    checksUsed: session.checksUsedCount,
    backspacesUsed: session.backspacesUsedCount,
    pointsEarned: score.total,
    pointsSpent: 0,
  };

  recordPuzzleCompletion(record);
  clearProgress(session.currentPuzzleId);
  updatePointsDisplay();

  // Check for new badges
  const badgesAfter = evaluateBadges(loadPlayerData());
  const newBadges = badgesAfter.filter((b) => !badgesBefore.has(b.id));

  // Trigger REZOLVAT stamp
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
    <p>Timp: ${timeStr} | Indicii: ${session.hintsUsedCount}</p>
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
  showCompletionCelebration(completionOverlay);
  console.log("PUZZLE SOLVED: REZOLVAT!");

  // Confetti celebration
  confetti({ particleCount: 80, spread: 70, origin: { x: 0.3, y: 0.6 } });
  confetti({ particleCount: 80, spread: 70, origin: { x: 0.7, y: 0.6 } });
}


// --- Load a puzzle ---
async function loadPuzzle(id: string): Promise<void> {
  try {
    puzzleListScrollY = window.scrollY;
    resetCompletionOverlay(completionOverlay);
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
    const { puzzle } = data;
    const template: boolean[][] = JSON.parse(puzzle.grid_template);
    gridInitialised = false; // force createGrid on next refresh
    cellHistory.clear();

    setPuzzleInteractionDisabled(false);

    // Restore saved progress if available
    const savedRaw = alreadySolved ? null : loadProgress(id);
    const saved = savedRaw && hasFilledCells(savedRaw) ? savedRaw : null;
    if (savedRaw && !saved) {
      clearProgress(id);
    }
    const sessionLoad = loadPuzzleSession(session, {
      detail: data,
      solutionJson: solutionResult.status === "fulfilled" ? solutionResult.value.solution : undefined,
      progress: saved,
      alreadySolved,
      touchRemoteEnabled,
      now: Date.now(),
    });
    if (sessionLoad.hydratedSolvedGrid) {
      renderPencilButton();
      setPuzzleInteractionDisabled(true);
    }

    const puzzleStatus = alreadySolved
      ? "Rezolvat"
      : sessionLoad.usedProgress
        ? "În curs"
        : "Disponibil";
    const extraMeta = [
      `${puzzle.grid_size}x${puzzle.grid_size}`,
      puzzleStatus,
    ];
    if (sessionLoad.usedProgress) {
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
    const loadedGrid = session.gridState;
    if (!loadedGrid) return;
    for (let r = 0; r < loadedGrid.size; r++) {
      for (let c = 0; c < loadedGrid.size; c++) {
        if (template[r][c]) {
          loadedGrid.activeRow = r;
          loadedGrid.activeCol = c;
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
  resetCompletionOverlay(completionOverlay);
  resetPuzzleSession(session);
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
  if (!session.gridState) return;
  if (session.gridState.isSolvedView) return;
  const result = checkPuzzle(session.gridState);
  if (!result.success) {
    if (result.reason === "not_enough_points") {
      showToast(
        `Nu ai suficiente puncte! Ai nevoie de ${result.cost} pts.`,
        "warning"
      );
    }
    return;
  }
  noteCheckUsed(session);
  updatePointsDisplay();
  refresh();
  if (result.wrong === 0 && result.empty === 0) {
    handleCompletion();
  } else {
    setTimeout(() => {
      if (!session.gridState) return;
      for (let r = 0; r < session.gridState.size; r++) {
        for (let c = 0; c < session.gridState.size; c++) {
          if (session.gridState.cells[r][c] === "!") {
            session.gridState.cells[r][c] = null;
          }
        }
      }
      refresh();
      saveCurrentProgress();
    }, 2000);
  }
});

btnHintLetter.addEventListener("click", () => {
  if (!session.gridState) return;
  if (session.gridState.isSolvedView) return;
  const result = revealLetter(session.gridState, session.currentDifficulty);
  if (result.success) {
    noteHintUsed(session);
    updatePointsDisplay();
    refresh();
    saveCurrentProgress();
    if (isPuzzleComplete(session.gridState)) {
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
  if (!session.gridState) return;
  if (session.gridState.isSolvedView) return;
  const result = revealWord(session.gridState, session.currentDifficulty);
  if (result.success) {
    noteHintUsed(session);
    updatePointsDisplay();
    refresh();
    saveCurrentProgress();
    if (isPuzzleComplete(session.gridState)) {
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
  showTab(activeTab);
});

btnPencil.addEventListener("click", async () => {
  if (isSolvedGridView()) return;
  if (!session.pencilMode) {
    const shouldEnable = await showPencilHelpIfNeeded();
    if (!shouldEnable) {
      session.pencilMode = false;
      renderPencilButton();
      return;
    }
    session.pencilMode = true;
    renderPencilButton();
    return;
  }

  session.pencilMode = false;
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
  if (!session.gridState) return;
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
