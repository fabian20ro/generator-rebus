import { loadPlayerData } from "../gamification/storage";
import { formatTime } from "../utils/format-time";

export interface StatisticsPanelContext {
  inProgressCount: number;
}

function getBestTime(): string {
  const data = loadPlayerData();
  if (data.puzzlesSolved.length === 0) {
    return "—";
  }
  return formatTime(
    Math.min(...data.puzzlesSolved.map((record) => record.timeSeconds))
  );
}

export function renderStatisticsPanel(
  container: HTMLElement,
  context: StatisticsPanelContext,
): void {
  const data = loadPlayerData();
  container.innerHTML = "";

  const summary = document.createElement("section");
  summary.className = "stats-section";
  summary.innerHTML = `
    <div class="stats-summary">
      <div class="stat-item">
        <span class="stat-value">${data.totalPoints}</span>
        <span class="stat-label">Puncte</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">${data.puzzlesSolved.length}</span>
        <span class="stat-label">Rezolvate</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">${context.inProgressCount}</span>
        <span class="stat-label">În curs</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">${getBestTime()}</span>
        <span class="stat-label">Cel mai bun timp</span>
      </div>
    </div>
  `;
  container.appendChild(summary);

  const historySection = document.createElement("section");
  historySection.className = "stats-section";

  const title = document.createElement("h2");
  title.className = "panel-title";
  title.textContent = "Istoric personal";
  historySection.appendChild(title);

  if (data.puzzlesSolved.length === 0) {
    const empty = document.createElement("p");
    empty.className = "history-empty";
    empty.textContent = "Primele tale rezolvări vor apărea aici.";
    historySection.appendChild(empty);
    container.appendChild(historySection);
    return;
  }

  const table = document.createElement("div");
  table.className = "history-table";

  for (const record of [...data.puzzlesSolved].reverse().slice(0, 20)) {
    const row = document.createElement("div");
    row.className = "history-row";
    row.innerHTML = `
      <span class="history-date">${new Date(record.completedAt).toLocaleDateString("ro-RO")}</span>
      <span class="history-time">${formatTime(record.timeSeconds)}</span>
      <span class="history-size">${record.gridSize}x${record.gridSize}</span>
      <span class="history-hints">${record.hintsUsed === 0 ? "Fără indicii" : `${record.hintsUsed} indicii`}</span>
      <span class="history-points">+${record.pointsEarned} pts</span>
    `;
    table.appendChild(row);
  }

  historySection.appendChild(table);
  container.appendChild(historySection);
}
