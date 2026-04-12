import {
  BADGE_DEFINITIONS,
  evaluateBadges,
  getLockedBadges,
  type Badge,
  type EarnedBadge,
} from "./badges";
import { loadPlayerData } from "./storage";

export interface RewardsPanelContext {
  inProgressCount: number;
}

function createBadgeCard(badge: Badge | EarnedBadge, unlocked: boolean): HTMLElement {
  const card = document.createElement("article");
  card.className = `badge-card ${unlocked ? "badge-card--unlocked" : "badge-card--locked"}`;
  card.innerHTML = `
    <div class="badge-icon">${unlocked ? badge.icon : "\uD83D\uDD12"}</div>
    <div class="badge-name">${badge.name}</div>
    <div class="badge-desc">${badge.description}</div>
  `;
  return card;
}

export function renderRewardsPanel(
  container: HTMLElement,
  _context: RewardsPanelContext,
): void {
  const data = loadPlayerData();
  const earned = evaluateBadges(data);
  const locked = getLockedBadges(data);

  container.innerHTML = "";

  const badgeSection = document.createElement("section");
  badgeSection.className = "stats-section";
  badgeSection.innerHTML = `<h2 class="panel-title">Insigne (${earned.length}/${BADGE_DEFINITIONS.length})</h2>`;

  const badgeGrid = document.createElement("div");
  badgeGrid.className = "badge-grid";
  for (const badge of earned) {
    badgeGrid.appendChild(createBadgeCard(badge, true));
  }
  for (const badge of locked) {
    badgeGrid.appendChild(createBadgeCard(badge, false));
  }
  badgeSection.appendChild(badgeGrid);
  container.appendChild(badgeSection);
}
