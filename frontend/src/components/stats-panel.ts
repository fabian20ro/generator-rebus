/**
 * Stats / Profile panel: shows points, badges, and local leaderboard.
 */

import { loadPlayerData, type PlayerData } from "../gamification/storage";
import {
  evaluateBadges,
  getLockedBadges,
  BADGE_DEFINITIONS,
  type EarnedBadge,
  type Badge,
} from "../gamification/badges";
import { formatTime } from "../utils/format-time";

export function renderStatsPanel(container: HTMLElement): void {
  const data = loadPlayerData();
  const earned = evaluateBadges(data);
  const locked = getLockedBadges(data);

  container.innerHTML = "";

  // --- Header ---
  const header = document.createElement("div");
  header.className = "stats-header";
  header.innerHTML = `
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
        <span class="stat-value">${earned.length}/${BADGE_DEFINITIONS.length}</span>
        <span class="stat-label">Insigne</span>
      </div>
    </div>
  `;
  container.appendChild(header);

  // --- Badges ---
  const badgesSection = document.createElement("div");
  badgesSection.className = "badges-section";

  const badgesTitle = document.createElement("h3");
  badgesTitle.textContent = "Insigne";
  badgesSection.appendChild(badgesTitle);

  const badgeGrid = document.createElement("div");
  badgeGrid.className = "badge-grid";

  for (const badge of earned) {
    badgeGrid.appendChild(createBadgeCard(badge, true));
  }
  for (const badge of locked) {
    badgeGrid.appendChild(createBadgeCard(badge, false));
  }

  badgesSection.appendChild(badgeGrid);
  container.appendChild(badgesSection);

  // --- Recent puzzles (local leaderboard) ---
  if (data.puzzlesSolved.length > 0) {
    const historySection = document.createElement("div");
    historySection.className = "history-section";

    const historyTitle = document.createElement("h3");
    historyTitle.textContent = "Istoric";
    historySection.appendChild(historyTitle);

    const table = document.createElement("div");
    table.className = "history-table";

    // Show most recent first, max 20
    const recent = [...data.puzzlesSolved].reverse().slice(0, 20);
    for (const record of recent) {
      const row = document.createElement("div");
      row.className = "history-row";
      const date = new Date(record.completedAt).toLocaleDateString("ro-RO");
      const time = formatTime(record.timeSeconds);
      row.innerHTML = `
        <span class="history-date">${date}</span>
        <span class="history-time">${time}</span>
        <span class="history-hints">${record.hintsUsed === 0 ? "Fără indicii" : record.hintsUsed + " indicii"}</span>
        <span class="history-points">+${record.pointsEarned} pts</span>
      `;
      table.appendChild(row);
    }

    historySection.appendChild(table);
    container.appendChild(historySection);
  }
}

function createBadgeCard(badge: Badge | EarnedBadge, unlocked: boolean): HTMLElement {
  const card = document.createElement("div");
  card.className = `badge-card ${unlocked ? "badge-card--unlocked" : "badge-card--locked"}`;

  card.innerHTML = `
    <div class="badge-icon">${unlocked ? badge.icon : "\uD83D\uDD12"}</div>
    <div class="badge-name">${badge.name}</div>
    <div class="badge-desc">${badge.description}</div>
  `;

  return card;
}

