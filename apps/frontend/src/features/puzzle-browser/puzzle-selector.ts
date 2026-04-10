import type { PuzzleTabItem, AvailableTabBrowseState } from "../gamification/puzzle-status";
import { PUZZLE_SIZE_OPTIONS, type AppTab } from "../gamification/puzzle-status";

export type PuzzleListMode = Extract<AppTab, "available" | "in_progress" | "solved">;

let sizeRowScrollLeft = 0;

function formatDateLabel(value?: string | null): string {
  if (!value) return "";
  const d = new Date(value);
  return (
    d.toLocaleDateString("ro-RO") + " " +
    d.toLocaleTimeString("ro-RO", { hour: "2-digit", minute: "2-digit" })
  );
}

function appendCardSelection(card: HTMLElement, onSelect: () => void): void {
  card.addEventListener("click", onSelect);
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect();
    }
  });
}

function createMetaRow(puzzle: PuzzleTabItem, mode: PuzzleListMode): string[] {
  const meta = [`Actualizat: ${formatDateLabel(puzzle.repaired_at || puzzle.created_at)}`];

  if (mode === "in_progress" && puzzle.savedAt) {
    meta.unshift(`Salvat: ${formatDateLabel(puzzle.savedAt)}`);
  }

  if (mode === "solved" && puzzle.solvedAt) {
    meta.unshift(`Rezolvat: ${formatDateLabel(puzzle.solvedAt)}`);
  }

  return meta;
}

function createPuzzleCard(
  puzzle: PuzzleTabItem,
  onSelect: (id: string) => void,
  mode: PuzzleListMode,
): HTMLElement {
  const card = document.createElement("article");
  card.className = `puzzle-card puzzle-card--${mode}`;
  card.setAttribute("role", "button");
  card.setAttribute("tabindex", "0");
  card.setAttribute("aria-label", `${puzzle.title || "Rebus"} — ${puzzle.grid_size}x${puzzle.grid_size}`);

  const subtitle = puzzle.description || puzzle.theme || "Fără descriere încă.";
  const meta = createMetaRow(puzzle, mode)
    .map((item) => `<span>${item}</span>`)
    .join("");

  card.innerHTML = `
    <div class="puzzle-card__top">
      <span class="puzzle-card__size">${puzzle.grid_size}x${puzzle.grid_size}</span>
    </div>
    <h3>${puzzle.title || "Rebus"}</h3>
    <p class="puzzle-card__theme">${subtitle}</p>
    <div class="puzzle-card__meta">${meta}</div>
  `;

  appendCardSelection(card, () => onSelect(puzzle.id));
  return card;
}

export function renderAvailableControls(
  container: HTMLElement,
  state: AvailableTabBrowseState,
  onSizeChange: (next: AvailableTabBrowseState["size"]) => void,
): void {
  container.innerHTML = "";

  const toolbar = document.createElement("div");
  toolbar.className = "selector-toolbar selector-toolbar--sizes";

  const scroller = document.createElement("div");
  scroller.className = "selector-size-scroller";

  const leftButton = document.createElement("button");
  leftButton.type = "button";
  leftButton.className = "selector-size-arrow selector-size-arrow--left";
  leftButton.textContent = "<";
  leftButton.setAttribute("aria-label", "Arată dimensiunile din stânga");

  const row = document.createElement("div");
  row.className = "selector-size-row";
  row.setAttribute("role", "group");
  row.setAttribute("aria-label", "Filtru dimensiune");

  const rightButton = document.createElement("button");
  rightButton.type = "button";
  rightButton.className = "selector-size-arrow selector-size-arrow--right";
  rightButton.textContent = ">";
  rightButton.setAttribute("aria-label", "Arată dimensiunile din dreapta");

  const handleSelection = (next: AvailableTabBrowseState["size"]): void => {
    sizeRowScrollLeft = row.scrollLeft;
    onSizeChange(next);
  };

  const allButton = document.createElement("button");
  allButton.type = "button";
  allButton.className = "size-chip";
  allButton.setAttribute("aria-pressed", String(state.size === "all"));
  if (state.size === "all") {
    allButton.classList.add("size-chip--active");
  }
  allButton.textContent = "Toate";
  allButton.title = "Toate dimensiunile";
  allButton.addEventListener("click", () => handleSelection("all"));
  row.appendChild(allButton);

  for (const size of PUZZLE_SIZE_OPTIONS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "size-chip";
    button.setAttribute("aria-pressed", String(state.size === size));
    if (state.size === size) {
      button.classList.add("size-chip--active");
    }
    button.textContent = String(size);
    button.title = `${size}x${size}`;
    button.addEventListener("click", () => handleSelection(size));
    row.appendChild(button);
  }

  const syncArrows = (): void => {
    const maxScrollLeft = Math.max(0, row.scrollWidth - row.clientWidth);
    const canScroll = maxScrollLeft > 4;
    scroller.classList.toggle("selector-size-scroller--overflow", canScroll);
    leftButton.classList.toggle("hidden", !canScroll);
    rightButton.classList.toggle("hidden", !canScroll);
    leftButton.disabled = row.scrollLeft <= 4;
    rightButton.disabled = row.scrollLeft >= maxScrollLeft - 4;
  };

  leftButton.addEventListener("click", () => {
    row.scrollBy({ left: -160, behavior: "smooth" });
  });

  rightButton.addEventListener("click", () => {
    row.scrollBy({ left: 160, behavior: "smooth" });
  });

  row.addEventListener("scroll", () => {
    sizeRowScrollLeft = row.scrollLeft;
    syncArrows();
  });

  scroller.appendChild(leftButton);
  scroller.appendChild(row);
  scroller.appendChild(rightButton);
  toolbar.appendChild(scroller);
  container.appendChild(toolbar);

  requestAnimationFrame(() => {
    row.scrollLeft = sizeRowScrollLeft;
    const active = row.querySelector<HTMLElement>(".size-chip--active");
    active?.scrollIntoView({ block: "nearest", inline: "nearest" });
    sizeRowScrollLeft = row.scrollLeft;
    syncArrows();
  });
}

export function renderPuzzleList(
  container: HTMLElement,
  puzzles: PuzzleTabItem[],
  onSelect: (id: string) => void,
  mode: PuzzleListMode,
  options?: {
    emptyTitle?: string;
    emptyBody?: string;
    emptyActionLabel?: string;
    onEmptyAction?: () => void;
  },
): void {
  container.innerHTML = "";

  if (puzzles.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `
      <h3>${options?.emptyTitle || "Nimic aici încă"}</h3>
      <p>${options?.emptyBody || "Revino după ce mai joci câteva rebusuri."}</p>
    `;

    if (options?.emptyActionLabel && options.onEmptyAction) {
      const action = document.createElement("button");
      action.type = "button";
      action.className = "size-chip";
      action.textContent = options.emptyActionLabel;
      action.addEventListener("click", options.onEmptyAction);
      empty.appendChild(action);
    }

    container.appendChild(empty);
    return;
  }

  for (const puzzle of puzzles) {
    container.appendChild(createPuzzleCard(puzzle, onSelect, mode));
  }
}
