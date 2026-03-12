/**
 * Active definition bar: shows the current clue definition above the grid.
 * Updates whenever the active cell/direction changes.
 */

import type { Clue } from "../db/puzzle-repository";
import type { GridState } from "./grid-renderer";
import { findActiveClue } from "./grid-renderer";

export function renderDefinitionBar(
  container: HTMLElement,
  state: GridState
): void {
  const clue = findActiveClue(state);
  if (!clue) {
    container.textContent = "";
    container.classList.add("definition-bar--empty");
    return;
  }

  container.classList.remove("definition-bar--empty");

  const dirLabel = clue.direction === "H" ? "Oriz." : "Vert.";
  container.innerHTML = "";

  const badge = document.createElement("span");
  badge.className = "definition-bar__badge";
  badge.textContent = `${clue.clue_number}${dirLabel}`;

  const text = document.createElement("span");
  text.className = "definition-bar__text";
  text.textContent = clue.definition;

  container.appendChild(badge);
  container.appendChild(text);
}
