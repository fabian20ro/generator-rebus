/**
 * Puzzle browse UI for the selector screen.
 * Main state/derivation lives in main.ts; this file only renders.
 */

import type { PuzzleSummary } from "../db/puzzle-repository";
import type { ChallengeStatus } from "../gamification/challenges";

export type PuzzleLocalStatus = "solved" | "in_progress" | "unplayed";
export type PuzzleStatusFilter = "all" | "in_progress" | "unsolved" | "solved";
export type PuzzleSizeGroup = "all" | "small" | "medium" | "large";
export type PuzzleSort = "recent" | "size_asc" | "size_desc" | "title";

export interface PuzzleBrowseState {
  status: PuzzleStatusFilter;
  hideCompleted: boolean;
  sizeGroup: PuzzleSizeGroup;
  sort: PuzzleSort;
  visibleCount?: number;
}

export interface PuzzleBrowseItem extends PuzzleSummary {
  localStatus: PuzzleLocalStatus;
  sizeGroup: Exclude<PuzzleSizeGroup, "all">;
  savedAt?: string | null;
}

export interface PuzzleBrowseSummary {
  totalCount: number;
  visibleCount: number;
  activeLabels: string[];
}

const SIZE_GROUPS: Array<{ value: PuzzleSizeGroup; label: string }> = [
  { value: "all", label: "Toate" },
  { value: "small", label: "Mic 7-9" },
  { value: "medium", label: "Mediu 10-12" },
  { value: "large", label: "Mare 13-15" },
];

const STATUS_FILTERS: Array<{ value: PuzzleStatusFilter; label: string }> = [
  { value: "all", label: "Toate" },
  { value: "in_progress", label: "În curs" },
  { value: "unsolved", label: "Nerezolvate" },
  { value: "solved", label: "Rezolvate" },
];

const SORT_OPTIONS: Array<{ value: PuzzleSort; label: string }> = [
  { value: "recent", label: "Recente" },
  { value: "size_asc", label: "Mărime ↑" },
  { value: "size_desc", label: "Mărime ↓" },
  { value: "title", label: "A-Z" },
];

let filtersExpanded = false;

function formatDateLabel(value?: string | null): string {
  if (!value) return "";
  const d = new Date(value);
  return (
    d.toLocaleDateString("ro-RO") + " " +
    d.toLocaleTimeString("ro-RO", { hour: "2-digit", minute: "2-digit" })
  );
}

function getStatusLabel(status: PuzzleLocalStatus): string {
  switch (status) {
    case "solved":
      return "Rezolvat";
    case "in_progress":
      return "În curs";
    default:
      return "Nou";
  }
}

function getSizeGroupLabel(group: Exclude<PuzzleSizeGroup, "all">): string {
  switch (group) {
    case "small":
      return "Mic 7-9";
    case "medium":
      return "Mediu 10-12";
    case "large":
      return "Mare 13-15";
  }
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

function createPuzzleCard(
  puzzle: PuzzleBrowseItem,
  onSelect: (id: string) => void,
  variant: "list" | "continue" = "list"
): HTMLElement {
  const card = document.createElement("article");
  card.className = "puzzle-card";
  card.classList.add(`puzzle-card--${variant}`);
  card.classList.add(`puzzle-card--status-${puzzle.localStatus}`);
  card.setAttribute("role", "button");
  card.setAttribute("tabindex", "0");

  const statusLabel = getStatusLabel(puzzle.localStatus);
  const updatedAt = formatDateLabel(puzzle.repaired_at || puzzle.created_at);
  const savedAt = formatDateLabel(puzzle.savedAt);
  const subtitle = puzzle.description || puzzle.theme || "Fără descriere încă.";
  const savedMeta = puzzle.localStatus === "in_progress" && savedAt
    ? `<span>Salvat: ${savedAt}</span>`
    : "";

  card.setAttribute(
    "aria-label",
    `${puzzle.title || "Rebus"} — ${statusLabel} — ${puzzle.grid_size}x${puzzle.grid_size}`
  );

  card.innerHTML = `
    <div class="puzzle-card__top">
      <span class="puzzle-card__status-badge puzzle-card__status-badge--${puzzle.localStatus}">${statusLabel}</span>
      <span class="puzzle-card__size">${puzzle.grid_size}x${puzzle.grid_size}</span>
    </div>
    <h3>${puzzle.title || "Rebus"}</h3>
    <p class="puzzle-card__theme">${subtitle}</p>
    <div class="puzzle-card__meta">
      <span>${getSizeGroupLabel(puzzle.sizeGroup)}</span>
      <span>Actualizat: ${updatedAt}</span>
      ${savedMeta}
    </div>
  `;

  appendCardSelection(card, () => onSelect(puzzle.id));
  return card;
}

export function getPuzzleSizeGroup(
  size: number
): Exclude<PuzzleSizeGroup, "all"> {
  if (size <= 9) return "small";
  if (size <= 12) return "medium";
  return "large";
}

export function renderBrowseControls(
  container: HTMLElement,
  state: PuzzleBrowseState,
  onStateChange: (patch: Partial<PuzzleBrowseState>) => void,
  onReset: () => void
): void {
  container.innerHTML = "";
  const activeFilterCount =
    Number(state.status !== "all") +
    Number(state.sizeGroup !== "all") +
    Number(state.hideCompleted);

  const sticky = document.createElement("div");
  sticky.className = "selector-toolbar";
  if (filtersExpanded) {
    sticky.classList.add("selector-toolbar--expanded");
  }

  const topRow = document.createElement("div");
  topRow.className = "selector-toolbar__top";

  const disclosure = document.createElement("button");
  disclosure.type = "button";
  disclosure.className = "browse-disclosure";
  if (activeFilterCount > 0) {
    disclosure.classList.add("browse-disclosure--active");
  }
  disclosure.textContent = activeFilterCount > 0
    ? `Filtre (${activeFilterCount})`
    : "Filtre";
  disclosure.setAttribute("aria-expanded", String(filtersExpanded));

  const panel = document.createElement("div");
  panel.className = "selector-toolbar__panel";
  if (!filtersExpanded) {
    panel.classList.add("hidden");
  }

  disclosure.addEventListener("click", () => {
    filtersExpanded = !filtersExpanded;
    panel.classList.toggle("hidden", !filtersExpanded);
    sticky.classList.toggle("selector-toolbar--expanded", filtersExpanded);
    disclosure.setAttribute("aria-expanded", String(filtersExpanded));
  });

  const sortWrap = document.createElement("label");
  sortWrap.className = "browse-select browse-select--compact";
  sortWrap.innerHTML = `<span class="selector-toolbar__label">Sortare</span>`;
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Sortare rebusuri");
  for (const option of SORT_OPTIONS) {
    const node = document.createElement("option");
    node.value = option.value;
    node.textContent = option.label;
    if (state.sort === option.value) {
      node.selected = true;
    }
    select.appendChild(node);
  }
  select.addEventListener("change", () => {
    onStateChange({ sort: select.value as PuzzleSort });
  });
  sortWrap.appendChild(select);

  const statusGroup = document.createElement("label");
  statusGroup.className = "browse-select";
  statusGroup.innerHTML = `<span class="selector-toolbar__label">Stare</span>`;
  const statusSelect = document.createElement("select");
  statusSelect.setAttribute("aria-label", "Filtru stare");
  for (const option of STATUS_FILTERS) {
    const node = document.createElement("option");
    node.value = option.value;
    node.textContent = option.label;
    if (state.status === option.value) {
      node.selected = true;
    }
    statusSelect.appendChild(node);
  }
  statusSelect.addEventListener("change", () => {
    const nextStatus = statusSelect.value as PuzzleStatusFilter;
    const patch: Partial<PuzzleBrowseState> = { status: nextStatus };
    if (nextStatus === "solved" && state.hideCompleted) {
      patch.hideCompleted = false;
    }
    onStateChange(patch);
  });
  statusGroup.appendChild(statusSelect);

  const sizeGroup = document.createElement("label");
  sizeGroup.className = "browse-select";
  sizeGroup.innerHTML = `<span class="selector-toolbar__label">Mărime</span>`;
  const sizeSelect = document.createElement("select");
  sizeSelect.setAttribute("aria-label", "Filtru mărime");
  for (const option of SIZE_GROUPS) {
    const node = document.createElement("option");
    node.value = option.value;
    node.textContent = option.label;
    if (state.sizeGroup === option.value) {
      node.selected = true;
    }
    sizeSelect.appendChild(node);
  }
  sizeSelect.addEventListener("change", () => {
    onStateChange({ sizeGroup: sizeSelect.value as PuzzleSizeGroup });
  });
  sizeGroup.appendChild(sizeSelect);

  const actions = document.createElement("div");
  actions.className = "selector-toolbar__actions";

  const hideCompleted = document.createElement("button");
  hideCompleted.type = "button";
  hideCompleted.className = "browse-toggle";
  if (state.hideCompleted) {
    hideCompleted.classList.add("browse-toggle--active");
  }
  hideCompleted.textContent = "Ascunde rezolvate";
  hideCompleted.setAttribute("aria-pressed", String(state.hideCompleted));
  hideCompleted.addEventListener("click", () => {
    onStateChange({ hideCompleted: !state.hideCompleted });
  });

  const reset = document.createElement("button");
  reset.type = "button";
  reset.className = "browse-reset";
  reset.textContent = "Resetează filtrele";
  reset.addEventListener("click", onReset);

  topRow.appendChild(disclosure);
  topRow.appendChild(sortWrap);

  actions.appendChild(hideCompleted);
  actions.appendChild(reset);

  panel.appendChild(statusGroup);
  panel.appendChild(sizeGroup);
  panel.appendChild(actions);

  sticky.appendChild(topRow);
  sticky.appendChild(panel);
  container.appendChild(sticky);
}

export function renderSelectorSummary(
  container: HTMLElement,
  summary: PuzzleBrowseSummary
): void {
  container.innerHTML = "";

  const copy = document.createElement("p");
  copy.className = "selector-summary__copy";
  copy.textContent =
    `${summary.visibleCount} din ${summary.totalCount} rebusuri vizibile`;
  container.appendChild(copy);

  if (summary.activeLabels.length > 0) {
    const tags = document.createElement("div");
    tags.className = "selector-summary__tags";
    for (const label of summary.activeLabels) {
      const tag = document.createElement("span");
      tag.className = "selector-summary__tag";
      tag.textContent = label;
      tags.appendChild(tag);
    }
    container.appendChild(tags);
  }
}

export function renderContinueSection(
  container: HTMLElement,
  puzzles: PuzzleBrowseItem[],
  onSelect: (id: string) => void
): void {
  container.innerHTML = "";
  if (puzzles.length === 0) {
    container.classList.add("hidden");
    return;
  }
  container.classList.remove("hidden");

  const title = document.createElement("h2");
  title.className = "selector-section__title";
  title.textContent = "Continuă";

  const subtitle = document.createElement("p");
  subtitle.className = "selector-section__subtitle";
  subtitle.textContent = "Reia rebusurile care au deja progres salvat.";

  const list = document.createElement("div");
  list.className = "puzzle-list puzzle-list--continue";
  for (const puzzle of puzzles) {
    list.appendChild(createPuzzleCard(puzzle, onSelect, "continue"));
  }

  container.appendChild(title);
  container.appendChild(subtitle);
  container.appendChild(list);
}

export function renderChallengeHighlight(
  container: HTMLElement,
  challenge: ChallengeStatus | null
): void {
  container.innerHTML = "";
  if (!challenge) {
    container.classList.add("hidden");
    return;
  }
  container.classList.remove("hidden");

  container.className = `selector-challenge ${challenge.done ? "selector-challenge--ready" : ""}`;
  container.innerHTML = `
    <p class="selector-challenge__eyebrow">Provocarea ta</p>
    <h2 class="selector-challenge__title">${challenge.title}</h2>
    <p class="selector-challenge__description">${challenge.description}</p>
    <span class="selector-challenge__progress">${challenge.progressLabel}</span>
  `;
}

export function renderPuzzleList(
  container: HTMLElement,
  puzzles: PuzzleBrowseItem[],
  onSelect: (id: string) => void,
  onReset: () => void,
  totalCount: number
): void {
  container.innerHTML = "";

  if (totalCount === 0) {
    container.innerHTML = "<p class=\"loading\">Nu sunt rebus-uri disponibile.</p>";
    return;
  }

  if (puzzles.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `
      <h3>Niciun rebus nu corespunde filtrelor</h3>
      <p>Schimbă filtrele active sau revino la lista completă.</p>
    `;
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "browse-reset";
    reset.textContent = "Resetează filtrele";
    reset.addEventListener("click", onReset);
    empty.appendChild(reset);
    container.appendChild(empty);
    return;
  }

  for (const puzzle of puzzles) {
    container.appendChild(createPuzzleCard(puzzle, onSelect));
  }
}
