/**
 * Renders the puzzle list for selecting which puzzle to play.
 */

import type { PuzzleSummary } from "../db/puzzle-repository";
import { isPuzzleAlreadySolved } from "../gamification/storage";
import { hasProgress } from "../gamification/progress-storage";

let activeFilter: number | null = null;

interface FilterRange {
  label: string;
  value: number | null;
  min: number;
  max: number;
}

const FILTERS: FilterRange[] = [
  { label: "Toate", value: null, min: 0, max: 5 },
  { label: "Ușor", value: 1, min: 1, max: 2 },
  { label: "Mediu", value: 3, min: 3, max: 3 },
  { label: "Dificil", value: 4, min: 4, max: 5 },
];

export function renderDifficultyFilter(
  container: HTMLElement,
  onFilterChange: () => void
): void {
  container.innerHTML = "";

  for (const f of FILTERS) {
    const btn = document.createElement("button");
    btn.className = "difficulty-filter__btn";
    if (activeFilter === f.value) {
      btn.classList.add("difficulty-filter__btn--active");
    }
    btn.textContent = f.label;
    btn.addEventListener("click", () => {
      activeFilter = f.value;
      renderDifficultyFilter(container, onFilterChange);
      onFilterChange();
    });
    container.appendChild(btn);
  }
}

function matchesFilter(difficulty: number): boolean {
  if (activeFilter === null) return true;
  const f = FILTERS.find((x) => x.value === activeFilter);
  if (!f) return true;
  return difficulty >= f.min && difficulty <= f.max;
}

export function renderPuzzleList(
  container: HTMLElement,
  puzzles: PuzzleSummary[],
  onSelect: (id: string) => void
): void {
  container.innerHTML = "";

  const filtered = puzzles.filter((p) => matchesFilter(p.difficulty));

  if (filtered.length === 0) {
    container.innerHTML = "<p>Nu sunt rebus-uri disponibile.</p>";
    return;
  }

  for (const puzzle of filtered) {
    const card = document.createElement("div");
    card.className = "puzzle-card";

    const solved = isPuzzleAlreadySolved(puzzle.id);
    const inProgress = !solved && hasProgress(puzzle.id);
    if (solved) {
      card.classList.add("puzzle-card--solved");
    } else if (inProgress) {
      card.classList.add("puzzle-card--in-progress");
    }

    const d = new Date(puzzle.created_at);
    const date = d.toLocaleDateString("ro-RO") + " " +
      d.toLocaleTimeString("ro-RO", { hour: "2-digit", minute: "2-digit" });
    const stars = "\u2605".repeat(puzzle.difficulty) +
      "\u2606".repeat(5 - puzzle.difficulty);
    const difficultyLabel = [
      "", "Ușor", "Simplu", "Mediu", "Dificil", "Expert",
    ][puzzle.difficulty] ?? "";

    const statusLabel = solved ? "rezolvat" : inProgress ? "în curs" : "nou";
    card.setAttribute("role", "button");
    card.setAttribute("tabindex", "0");
    card.setAttribute("aria-label", `${puzzle.title || "Rebus"} — ${statusLabel}`);

    card.innerHTML = `
      <span class="puzzle-card__size">${puzzle.grid_size}x${puzzle.grid_size}</span>
      <h3>${solved ? "\u2713 " : inProgress ? "\u25B6 " : ""}${puzzle.title || "Rebus"}</h3>
      <p class="puzzle-card__theme">${puzzle.theme || ""}</p>
      <div class="puzzle-card__meta">
        <span title="${difficultyLabel}">${stars}</span>
        <span>${date}</span>
      </div>
    `;

    card.addEventListener("click", () => onSelect(puzzle.id));
    container.appendChild(card);
  }
}
