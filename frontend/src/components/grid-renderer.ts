/**
 * Renders the crossword grid as a CSS Grid of input cells.
 */

import type { Clue } from "../db/puzzle-repository";

export interface GridState {
  size: number;
  template: boolean[][]; // true = letter, false = black
  cells: (string | null)[][]; // user input
  solution: (string | null)[][] | null;
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
  for (let r = 0; r < size; r++) {
    const row: (string | null)[] = [];
    for (let c = 0; c < size; c++) {
      row.push(template[r][c] ? null : "#");
    }
    cells.push(row);
  }

  return {
    size,
    template,
    cells,
    solution: null,
    clues,
    activeRow: -1,
    activeCol: -1,
    activeDirection: "H",
  };
}

/**
 * Compute clue numbers for cells that start a horizontal or vertical word.
 */
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

export function renderGrid(
  container: HTMLElement,
  state: GridState,
  onCellClick: (row: number, col: number) => void,
  onCellInput: (row: number, col: number, value: string) => void,
  onKeyDown: (row: number, col: number, e: KeyboardEvent) => void
): void {
  container.innerHTML = "";
  container.style.gridTemplateColumns = `repeat(${state.size}, 1fr)`;

  const clueNumbers = computeClueNumbers(state.clues);
  const activeSlotCells = getActiveSlotCells(state);

  for (let r = 0; r < state.size; r++) {
    for (let c = 0; c < state.size; c++) {
      const cell = document.createElement("div");
      cell.className = "cell";
      cell.dataset.row = String(r);
      cell.dataset.col = String(c);

      if (!state.template[r][c]) {
        cell.classList.add("cell--black");
      } else {
        cell.classList.add("cell--letter");

        if (r === state.activeRow && c === state.activeCol) {
          cell.classList.add("cell--active");
        } else if (activeSlotCells.has(`${r},${c}`)) {
          cell.classList.add("cell--highlight");
        }

        // Clue number
        const nums = clueNumbers.get(`${r},${c}`);
        if (nums) {
          const span = document.createElement("span");
          span.className = "cell__number";
          span.textContent = nums.join(",");
          cell.appendChild(span);
        }

        // Input
        const input = document.createElement("input");
        input.type = "text";
        input.maxLength = 1;
        input.className = "cell__input";
        input.value = state.cells[r][c] || "";
        input.dataset.row = String(r);
        input.dataset.col = String(c);
        input.autocomplete = "off";
        input.autocapitalize = "characters";

        if (state.cells[r][c] === "!" ) {
          cell.classList.add("cell--wrong");
        }
        if (state.cells[r][c] && state.cells[r][c] !== "!") {
          // Check if this was revealed by hint
          if (cell.dataset.revealed === "true") {
            cell.classList.add("cell--revealed");
          }
        }

        input.addEventListener("click", (e) => {
          e.stopPropagation();
          onCellClick(r, c);
        });

        input.addEventListener("input", () => {
          const val = input.value.toUpperCase().replace(/[^A-Z]/g, "");
          input.value = val;
          onCellInput(r, c, val);
        });

        input.addEventListener("keydown", (e) => {
          onKeyDown(r, c, e);
        });

        cell.appendChild(input);
      }

      container.appendChild(cell);
    }
  }
}

function getActiveSlotCells(state: GridState): Set<string> {
  const cells = new Set<string>();
  if (state.activeRow < 0 || state.activeCol < 0) return cells;

  // Find the active clue
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
  col: number
): void {
  const input = container.querySelector(
    `input[data-row="${row}"][data-col="${col}"]`
  ) as HTMLInputElement | null;
  input?.focus();
}
