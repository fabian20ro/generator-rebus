/**
 * Font size scaler for the clue list.
 * Renders +/- controls and persists preference in localStorage.
 */

const STORAGE_KEY = "rebus_clue_font_size";
const DEFAULT_SIZE = 0.9;
const MIN_SIZE = 0.7;
const MAX_SIZE = 1.5;
const STEP = 0.1;

function getSavedSize(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SIZE;
    const val = parseFloat(raw);
    if (isNaN(val)) return DEFAULT_SIZE;
    return Math.max(MIN_SIZE, Math.min(MAX_SIZE, val));
  } catch {
    return DEFAULT_SIZE;
  }
}

function applySize(size: number): void {
  document.documentElement.style.setProperty(
    "--clue-font-size",
    `${size}rem`
  );
}

function saveSize(size: number): void {
  localStorage.setItem(STORAGE_KEY, String(size));
}

/** Apply saved font size immediately (call at app init to avoid flash). */
export function applySavedFontSize(): void {
  applySize(getSavedSize());
}

/** Render font scaler controls into the given container (prepended). */
export function initFontScaler(container: HTMLElement): void {
  // Don't double-initialize
  if (container.querySelector(".font-scaler")) return;

  let currentSize = getSavedSize();
  applySize(currentSize);

  const bar = document.createElement("div");
  bar.className = "font-scaler";

  const btnMinus = document.createElement("button");
  btnMinus.className = "font-scaler__btn";
  btnMinus.textContent = "\u2212"; // minus sign
  btnMinus.title = "Text mai mic";

  const label = document.createElement("span");
  label.className = "font-scaler__label";

  const btnPlus = document.createElement("button");
  btnPlus.className = "font-scaler__btn";
  btnPlus.textContent = "+";
  btnPlus.title = "Text mai mare";

  function updateLabel(): void {
    const pct = Math.round((currentSize / DEFAULT_SIZE) * 100);
    label.textContent = `${pct}%`;
  }

  function adjust(delta: number): void {
    currentSize = Math.round((currentSize + delta) * 10) / 10;
    currentSize = Math.max(MIN_SIZE, Math.min(MAX_SIZE, currentSize));
    applySize(currentSize);
    saveSize(currentSize);
    updateLabel();
  }

  btnMinus.addEventListener("click", () => adjust(-STEP));
  btnPlus.addEventListener("click", () => adjust(STEP));

  bar.appendChild(btnMinus);
  bar.appendChild(label);
  bar.appendChild(btnPlus);

  updateLabel();
  container.prepend(bar);
}
