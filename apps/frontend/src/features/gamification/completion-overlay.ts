export interface CompletionOverlayElements {
  completionModal: HTMLElement;
  stampContainer: HTMLElement;
  completionDetails?: HTMLElement | null;
}

export function resetCompletionOverlay(
  elements: CompletionOverlayElements
): void {
  elements.completionModal.classList.add("hidden");
  elements.stampContainer.classList.add("hidden");
  elements.stampContainer.classList.remove("animate-stamp");
  if (elements.completionDetails) {
    elements.completionDetails.innerHTML = "";
  }
}

export function showCompletionCelebration(
  elements: CompletionOverlayElements
): void {
  resetCompletionOverlay(elements);
  void elements.stampContainer.offsetWidth;
  elements.stampContainer.classList.remove("hidden");
  elements.stampContainer.classList.add("animate-stamp");
  elements.completionModal.classList.remove("hidden");
}
