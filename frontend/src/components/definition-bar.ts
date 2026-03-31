/**
 * Active definition bar: shows the current clue definition above the grid.
 * Updates whenever the active cell/direction changes.
 */

import type { Clue } from "../db/puzzle-repository";
import type { GridState } from "./grid-renderer";
import { findActiveClue } from "./grid-renderer";

const MAX_FONT_SIZE_REM = 1.05;
const MIN_FONT_SIZE_REM = 0.66;
const FONT_STEP_REM = 0.04;

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
  badge.textContent = `${clue.clue_number}${dirLabel}`;

  const text = document.createElement("span");
  text.className = "definition-bar__text";
  text.textContent = clue.definition;

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
