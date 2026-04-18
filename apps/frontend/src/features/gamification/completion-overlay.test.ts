/** @jest-environment jsdom */

import {
  resetCompletionOverlay,
  showCompletionCelebration,
} from "./completion-overlay";

function setupDom() {
  document.body.innerHTML = `
    <div id="completion-modal">
      <div id="completion-details"><p>old</p></div>
    </div>
    <div id="stamp-container" class="animate-stamp"></div>
  `;

  return {
    completionModal: document.getElementById("completion-modal") as HTMLElement,
    completionDetails: document.getElementById("completion-details") as HTMLElement,
    stampContainer: document.getElementById("stamp-container") as HTMLElement,
  };
}

describe("completion overlay state", () => {
  test("reset hides modal and stamp and clears stamp animation", () => {
    const elements = setupDom();

    resetCompletionOverlay(elements);

    expect(elements.completionModal.classList.contains("hidden")).toBe(true);
    expect(elements.stampContainer.classList.contains("hidden")).toBe(true);
    expect(elements.stampContainer.classList.contains("animate-stamp")).toBe(false);
    expect(elements.completionDetails.innerHTML).toBe("");
  });

  test("show after reset replays the celebration state cleanly", () => {
    const elements = setupDom();

    resetCompletionOverlay(elements);
    showCompletionCelebration(elements);

    expect(elements.completionModal.classList.contains("hidden")).toBe(false);
    expect(elements.stampContainer.classList.contains("hidden")).toBe(false);
    expect(elements.stampContainer.classList.contains("animate-stamp")).toBe(true);
  });

  test("reset clears stale celebration state before another puzzle opens", () => {
    const elements = setupDom();

    showCompletionCelebration(elements);
    resetCompletionOverlay(elements);

    expect(elements.completionModal.classList.contains("hidden")).toBe(true);
    expect(elements.stampContainer.classList.contains("hidden")).toBe(true);
    expect(elements.stampContainer.classList.contains("animate-stamp")).toBe(false);
  });
});
