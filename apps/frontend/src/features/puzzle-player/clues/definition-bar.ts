/**
 * Active definition bar: shows the current clue definition above the grid.
 * Updates whenever the active cell/direction changes.
 */

import type { Clue } from "../../../shared/types/puzzle";
import type { GridState } from "../grid/grid-renderer";
import { findActiveClue } from "../grid/grid-renderer";

const MAX_FONT_SIZE_REM = 1.25;
const MIN_FONT_SIZE_REM = 0.72;
const FONT_STEP_REM = 0.02;

export function renderDefinitionBar(
  container: HTMLElement,
  state: GridState
): void {
  const clue = findActiveClue(state);
  if (!clue) {
    container.textContent = "";
    container.classList.add("definition-bar--empty");
    container.style.removeProperty("--definition-font-size");
    return;
  }

  container.classList.remove("definition-bar--empty");

  const dirLabel = clue.direction === "H" ? "Oriz." : "Vert.";
  container.innerHTML = "";

  const badge = document.createElement("span");
  badge.className = "definition-bar__badge";

  const badgeDirection = document.createElement("span");
  badgeDirection.className = "definition-bar__direction";
  badgeDirection.textContent = dirLabel;

  const badgeNumber = document.createElement("span");
  badgeNumber.className = "definition-bar__number";
  badgeNumber.textContent = String(clue.clue_number);

  const text = document.createElement("span");
  text.className = "definition-bar__text";
  text.textContent = clue.definition;

  badge.appendChild(badgeDirection);
  badge.appendChild(badgeNumber);
  container.appendChild(badge);
  container.appendChild(text);
  fitDefinitionText(container, text);
}

function fitDefinitionText(container: HTMLElement, text: HTMLElement): void {
  let fontSize = MAX_FONT_SIZE_REM;
  text.style.fontSize = `${fontSize}rem`;

  while (fontSize > MIN_FONT_SIZE_REM && text.scrollHeight > text.clientHeight + 1) {
    fontSize = Math.max(MIN_FONT_SIZE_REM, fontSize - FONT_STEP_REM);
    text.style.fontSize = `${fontSize}rem`;
  }

  container.style.setProperty("--definition-font-size", `${fontSize}rem`);
}
