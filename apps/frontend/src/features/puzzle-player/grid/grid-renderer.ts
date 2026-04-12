/**
 * Renders the crossword grid as a CSS Grid of input cells.
 *
 * Optimised: DOM elements are created once (createGrid) and then
 * diff-patched on every state change (updateGrid). Event listeners
 * use delegation on the container so they are never recreated.
 */

import type { Clue } from "../../../shared/types/puzzle";

export interface GridState {
  size: number;
  template: boolean[][]; // true = letter, false = black
  cells: (string | null)[][]; // user input
  revealed: boolean[][]; // true = revealed by hint
  pencilCells: boolean[][]; // true = entered in pencil mode
  solution: (string | null)[][] | null;
  isSolvedView: boolean;
  touchRemoteEnabled: boolean;
  clues: Clue[];
  activeRow: number;
  activeCol: number;
  activeDirection: "H" | "V";
}

export function createGridState(
  size: number,
  template: boolean[][],
  clues: Clue[]
): GridState {
  const cells: (string | null)[][] = [];
  const revealed: boolean[][] = [];
  const pencilCells: boolean[][] = [];
  for (let r = 0; r < size; r++) {
    const row: (string | null)[] = [];
    const revRow: boolean[] = [];
    const penRow: boolean[] = [];
    for (let c = 0; c < size; c++) {
      row.push(template[r][c] ? null : "#");
      revRow.push(false);
      penRow.push(false);
    }
    cells.push(row);
    revealed.push(revRow);
    pencilCells.push(penRow);
  }

  return {
    size,
    template,
    cells,
    revealed,
    pencilCells,
    solution: null,
    isSolvedView: false,
    touchRemoteEnabled: false,
    clues,
    activeRow: -1,
    activeCol: -1,
    activeDirection: "H",
  };
}

// ---------------------------------------------------------------------------
// Module-level state for the current grid instance
// ---------------------------------------------------------------------------

/** O(1) cell lookup by "r,c" key. */
const cellRefs = new Map<
  string,
  { cell: HTMLElement; input: HTMLInputElement }
>();

/** Cache to prevent unnecessary DOM updates. Maps "r,c" to a serialized state string. */
const cellRenderCache = new Map<string, string>();

/** Stored callbacks set during createGrid. */
let storedOnCellClick: ((row: number, col: number) => void) | null = null;
let storedOnCellInput: ((row: number, col: number, value: string) => void) | null = null;
let storedOnKeyDown: ((row: number, col: number, e: KeyboardEvent) => void) | null = null;

/** The container that currently has delegated listeners attached. */
let delegatedContainer: HTMLElement | null = null;

/** The grid size used when the DOM was last created. */
let createdSize = -1;

// ---------------------------------------------------------------------------
// Clue numbers (stable between renders for the same puzzle)
// ---------------------------------------------------------------------------

function computeClueNumbers(clues: Clue[]): Map<string, number[]> {
  const map = new Map<string, number[]>();
  for (const clue of clues) {
    const key = `${clue.start_row},${clue.start_col}`;
    const existing = map.get(key) || [];
    existing.push(clue.clue_number);
    map.set(key, existing);
  }
  return map;
}

// ---------------------------------------------------------------------------
// Event delegation helpers
// ---------------------------------------------------------------------------

function findInputFromEvent(e: Event): HTMLInputElement | null {
  const target = e.target as HTMLElement;
  if (target.tagName === "INPUT" && target.dataset.row != null) {
    return target as HTMLInputElement;
  }
  return null;
}

function handleDelegatedClick(e: Event): void {
  const input = findInputFromEvent(e);
  if (!input || !storedOnCellClick) return;
  e.stopPropagation();
  storedOnCellClick(Number(input.dataset.row), Number(input.dataset.col));
}

function handleDelegatedInput(e: Event): void {
  const input = findInputFromEvent(e);
  if (!input || !storedOnCellInput) return;
  const val = input.value.toUpperCase().replace(/[^A-Z]/g, "");
  input.value = val;
  storedOnCellInput(
    Number(input.dataset.row),
    Number(input.dataset.col),
    val
  );
}

function handleDelegatedKeyDown(e: Event): void {
  const input = findInputFromEvent(e);
  if (!input || !storedOnKeyDown) return;
  storedOnKeyDown(
    Number(input.dataset.row),
    Number(input.dataset.col),
    e as KeyboardEvent
  );
}

// ---------------------------------------------------------------------------
// createGrid — build all DOM elements once, attach delegated listeners
// ---------------------------------------------------------------------------

export function createGrid(
  container: HTMLElement,
  state: GridState,
  onCellClick: (row: number, col: number) => void,
  onCellInput: (row: number, col: number, value: string) => void,
  onKeyDown: (row: number, col: number, e: KeyboardEvent) => void
): void {
  // Remove old delegated listeners if any
  if (delegatedContainer) {
    delegatedContainer.removeEventListener("click", handleDelegatedClick);
    delegatedContainer.removeEventListener("input", handleDelegatedInput);
    delegatedContainer.removeEventListener("keydown", handleDelegatedKeyDown);
  }

  // Store callbacks
  storedOnCellClick = onCellClick;
  storedOnCellInput = onCellInput;
  storedOnKeyDown = onKeyDown;

  // Clear previous DOM
  container.innerHTML = "";
  cellRefs.clear();
  createdSize = state.size;
  cellRenderCache.clear();

  container.style.gridTemplateColumns = `repeat(${state.size}, 1fr)`;

  const clueNumbers = computeClueNumbers(state.clues);

  for (let r = 0; r < state.size; r++) {
    for (let c = 0; c < state.size; c++) {
      const cell = document.createElement("div");
      cell.className = "cell";
      cell.dataset.row = String(r);
      cell.dataset.col = String(c);
      cell.setAttribute("role", "gridcell");
      cell.tabIndex = -1;

      if (!state.template[r][c]) {
        cell.classList.add("cell--black");
        container.appendChild(cell);
        continue;
      }

      cell.classList.add("cell--letter");

      // Clue number badge (static — never changes)
      const nums = clueNumbers.get(`${r},${c}`);
      if (nums) {
        const span = document.createElement("span");
        span.className = "cell__number";
        span.textContent = nums.join(",");
        cell.appendChild(span);
      }

      // Input element
      const input = document.createElement("input");
      input.type = "text";
      input.maxLength = 1;
      input.className = "cell__input";
      input.dataset.row = String(r);
      input.dataset.col = String(c);
      input.autocomplete = "off";
      input.autocapitalize = "characters";
      input.inputMode = state.touchRemoteEnabled ? "none" : "text";
      input.setAttribute("aria-label", `Rând ${r + 1}, Coloană ${c + 1}`);
      input.readOnly = state.isSolvedView || state.touchRemoteEnabled;

      cell.appendChild(input);
      container.appendChild(cell);

      cellRefs.set(`${r},${c}`, { cell, input });
    }
  }

  // Attach delegated listeners
  container.addEventListener("click", handleDelegatedClick);
  container.addEventListener("input", handleDelegatedInput);
  container.addEventListener("keydown", handleDelegatedKeyDown);
  delegatedContainer = container;

  // Apply initial visual state
  updateGrid(container, state);
}

// ---------------------------------------------------------------------------
// updateGrid — diff-patch classes and values without touching DOM structure
// ---------------------------------------------------------------------------

export function updateGrid(
  _container: HTMLElement,
  state: GridState
): void {
  const activeSlotCells = getActiveSlotCells(state);

  for (let r = 0; r < state.size; r++) {
    for (let c = 0; c < state.size; c++) {
      const ref = cellRefs.get(`${r},${c}`);
      if (!ref) continue; // black cell — nothing dynamic

      const { cell, input } = ref;
      const cellValue = state.cells[r][c];

      // Toggle dynamic classes
      const isActive = r === state.activeRow && c === state.activeCol;
      const isHighlight = !isActive && activeSlotCells.has(`${r},${c}`);
      const isWrong = cellValue === "!";
      const isRevealed =
        !!cellValue && cellValue !== "!" && state.revealed[r][c];

      const isPencil =
        !!cellValue && cellValue !== "!" && state.pencilCells[r][c];

      const displayVal = cellValue && cellValue !== "!" && cellValue !== "#" ? cellValue : "";
      const inputMode = state.touchRemoteEnabled ? "none" : "text";
      const readOnly = state.isSolvedView || state.touchRemoteEnabled;

      // Serialize the presentation state
      const stateString = `${isActive}|${isHighlight}|${isWrong}|${isRevealed}|${isPencil}|${displayVal}|${inputMode}|${readOnly}`;

      // Skip DOM updates if nothing visual changed
      if (cellRenderCache.get(`${r},${c}`) === stateString) continue;
      cellRenderCache.set(`${r},${c}`, stateString);

      cell.classList.toggle("cell--active", isActive);
      cell.classList.toggle("cell--highlight", isHighlight);
      cell.classList.toggle("cell--wrong", isWrong);
      cell.classList.toggle("cell--revealed", isRevealed);
      cell.classList.toggle("cell--pencil", isPencil);

      if (isActive) {
        input.setAttribute("aria-current", "true");
      } else {
        input.removeAttribute("aria-current");
      }

      input.inputMode = inputMode;
      input.readOnly = readOnly;

      // Update input value only when it differs (avoids cursor jump)
      if (input.value !== displayVal) {
        input.value = displayVal;
        
        // Trigger ink animation if a character was entered
        if (displayVal && !isRevealed) {
          input.classList.remove("animate-ink");
          // Trigger reflow
          void input.offsetWidth;
          input.classList.add("animate-ink");
        }
      }

      // Procedural Smudges (1.5% chance, deterministic based on r/c)
      const smudgeSeed = (r * 13 + c * 31) % 100;
      const hasSmudge = smudgeSeed < 2;
      cell.classList.toggle("cell--smudge", hasSmudge);
      if (hasSmudge) {
        cell.classList.toggle("cell--smudge-tr", smudgeSeed === 0);
        cell.classList.toggle("cell--smudge-bl", smudgeSeed === 1);
      }
    }
  }
}

// ---------------------------------------------------------------------------
// renderGrid — backward-compatible wrapper (auto-detects create vs update)
// ---------------------------------------------------------------------------

export function renderGrid(
  container: HTMLElement,
  state: GridState,
  onCellClick: (row: number, col: number) => void,
  onCellInput: (row: number, col: number, value: string) => void,
  onKeyDown: (row: number, col: number, e: KeyboardEvent) => void
): void {
  if (createdSize !== state.size || delegatedContainer !== container) {
    createGrid(container, state, onCellClick, onCellInput, onKeyDown);
  } else {
    // Callbacks may have changed (closures over gridState), update them
    storedOnCellClick = onCellClick;
    storedOnCellInput = onCellInput;
    storedOnKeyDown = onKeyDown;
    updateGrid(container, state);
  }
}

// ---------------------------------------------------------------------------
// Active-slot helpers (unchanged logic)
// ---------------------------------------------------------------------------

function getActiveSlotCells(state: GridState): Set<string> {
  const cells = new Set<string>();
  if (state.activeRow < 0 || state.activeCol < 0) return cells;

  const activeClue = findActiveClue(state);
  if (!activeClue) return cells;

  for (let i = 0; i < activeClue.length; i++) {
    const r =
      activeClue.direction === "H"
        ? activeClue.start_row
        : activeClue.start_row + i;
    const c =
      activeClue.direction === "H"
        ? activeClue.start_col + i
        : activeClue.start_col;
    cells.add(`${r},${c}`);
  }

  return cells;
}

export function findActiveClue(state: GridState): Clue | undefined {
  return state.clues.find((clue) => {
    if (clue.direction !== state.activeDirection) return false;
    if (clue.direction === "H") {
      return (
        state.activeRow === clue.start_row &&
        state.activeCol >= clue.start_col &&
        state.activeCol < clue.start_col + clue.length
      );
    } else {
      return (
        state.activeCol === clue.start_col &&
        state.activeRow >= clue.start_row &&
        state.activeRow < clue.start_row + clue.length
      );
    }
  });
}

export function focusCell(
  container: HTMLElement,
  row: number,
  col: number,
  options: { native?: boolean } = {}
): void {
  const ref = cellRefs.get(`${row},${col}`);
  if (ref) {
    const target = options.native === false ? ref.cell : ref.input;
    try {
      target.focus({ preventScroll: true });
    } catch {
      target.focus();
    }
  }
}
