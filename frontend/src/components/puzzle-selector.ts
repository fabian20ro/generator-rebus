/**
 * Renders the puzzle list for selecting which puzzle to play.
 */

import type { PuzzleSummary } from "../db/puzzle-repository";
import { isPuzzleAlreadySolved } from "../gamification/storage";
import { hasProgress } from "../gamification/progress-storage";

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
