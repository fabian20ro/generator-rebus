/**
 * Renders the puzzle list for selecting which puzzle to play.
 */

import type { PuzzleSummary } from "../db/puzzle-repository";
import { isPuzzleAlreadySolved } from "../gamification/storage";

export function renderPuzzleList(
  container: HTMLElement,
  puzzles: PuzzleSummary[],
  onSelect: (id: string) => void
): void {
  container.innerHTML = "";

  if (puzzles.length === 0) {
    container.innerHTML = "<p>Nu sunt rebus-uri disponibile.</p>";
    return;
  }

  for (const puzzle of puzzles) {
    const card = document.createElement("div");
    card.className = "puzzle-card";

    const solved = isPuzzleAlreadySolved(puzzle.id);
    if (solved) {
      card.classList.add("puzzle-card--solved");
    }

    const date = new Date(puzzle.created_at).toLocaleDateString("ro-RO");
    const stars = "\u2605".repeat(puzzle.difficulty) +
      "\u2606".repeat(5 - puzzle.difficulty);

    card.innerHTML = `
      <h3>${solved ? "\u2713 " : ""}${puzzle.title || "Rebus"}</h3>
      <p class="puzzle-card__theme">${puzzle.theme || ""}</p>
      <div class="puzzle-card__meta">
        <span>${puzzle.grid_size}x${puzzle.grid_size}</span>
        <span>${stars}</span>
        <span>${date}</span>
      </div>
    `;

    card.addEventListener("click", () => onSelect(puzzle.id));
    container.appendChild(card);
  }
}
