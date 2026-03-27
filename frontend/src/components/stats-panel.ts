/**
 * Progress panel: points, current goals, badges, and personal history.
 */

import { loadPlayerData } from "../gamification/storage";
import type { ChallengeStatus } from "../gamification/challenges";
import {
  evaluateBadges,
  getLockedBadges,
  BADGE_DEFINITIONS,
  type EarnedBadge,
  type Badge,
} from "../gamification/badges";
import { formatTime } from "../utils/format-time";

export interface StatsPanelContext {
  inProgressCount: number;
  challenges: ChallengeStatus[];
}

export function renderStatsPanel(
  container: HTMLElement,
  context: StatsPanelContext
): void {
  const data = loadPlayerData();
  const earned = evaluateBadges(data);
  const locked = getLockedBadges(data);
  const bestTime = getBestTime(data);

  container.innerHTML = "";

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
        <span class="stat-value">${context.inProgressCount}</span>
        <span class="stat-label">În curs</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">${bestTime}</span>
        <span class="stat-label">Cel mai bun timp</span>
      </div>
    </div>
  `;
  container.appendChild(header);

  const challengesSection = document.createElement("section");
  challengesSection.className = "challenge-section";

  const challengeTitle = document.createElement("h3");
  challengeTitle.textContent = "Provocări";
  challengesSection.appendChild(challengeTitle);

  const challengeGrid = document.createElement("div");
  challengeGrid.className = "challenge-grid";
  for (const challenge of context.challenges) {
    challengeGrid.appendChild(createChallengeCard(challenge));
  }
  challengesSection.appendChild(challengeGrid);
  container.appendChild(challengesSection);

  const badgesSection = document.createElement("section");
  badgesSection.className = "badges-section";

  const badgesTitle = document.createElement("h3");
  badgesTitle.textContent = `Insigne (${earned.length}/${BADGE_DEFINITIONS.length})`;
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

  const historySection = document.createElement("section");
  historySection.className = "history-section";

  const historyTitle = document.createElement("h3");
  historyTitle.textContent = "Istoric personal";
  historySection.appendChild(historyTitle);

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

  const recent = [...data.puzzlesSolved].reverse().slice(0, 20);
  for (const record of recent) {
    const row = document.createElement("div");
    row.className = "history-row";
    const date = new Date(record.completedAt).toLocaleDateString("ro-RO");
    row.innerHTML = `
      <span class="history-date">${date}</span>
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

function getBestTime(data: ReturnType<typeof loadPlayerData>): string {
  if (data.puzzlesSolved.length === 0) {
    return "—";
  }
  const best = Math.min(...data.puzzlesSolved.map((record) => record.timeSeconds));
  return formatTime(best);
}

function createChallengeCard(challenge: ChallengeStatus): HTMLElement {
  const card = document.createElement("article");
  card.className = "challenge-card";
  if (challenge.done) {
    card.classList.add("challenge-card--done");
  }

  card.innerHTML = `
    <div class="challenge-card__top">
      <span class="challenge-card__status">${challenge.done ? "Pregătit" : "În lucru"}</span>
      <span class="challenge-card__progress">${challenge.progressLabel}</span>
    </div>
    <div class="challenge-card__title">${challenge.title}</div>
    <div class="challenge-card__desc">${challenge.description}</div>
  `;

  return card;
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
