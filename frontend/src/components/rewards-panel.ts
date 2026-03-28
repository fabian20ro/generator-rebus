import { deriveChallenges } from "../gamification/challenges";
import {
  BADGE_DEFINITIONS,
  evaluateBadges,
  getLockedBadges,
  type Badge,
  type EarnedBadge,
} from "../gamification/badges";
import { loadPlayerData } from "../gamification/storage";

export interface RewardsPanelContext {
  inProgressCount: number;
}

function createChallengeCard(challenge: ReturnType<typeof deriveChallenges>[number]): HTMLElement {
  const card = document.createElement("article");
  card.className = "challenge-card";
  if (challenge.done) {
    card.classList.add("challenge-card--done");
  }

  card.innerHTML = `
    <div class="challenge-card__top">
      <span class="challenge-card__status">${challenge.done ? "Gata" : "În lucru"}</span>
      <span class="challenge-card__progress">${challenge.progressLabel}</span>
    </div>
    <div class="challenge-card__title">${challenge.title}</div>
    <div class="challenge-card__desc">${challenge.description}</div>
  `;

  return card;
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
  context: RewardsPanelContext,
): void {
  const data = loadPlayerData();
  const earned = evaluateBadges(data);
  const locked = getLockedBadges(data);
  const challenges = deriveChallenges(data, context.inProgressCount);

  container.innerHTML = "";

  const challengeSection = document.createElement("section");
  challengeSection.className = "stats-section";
  challengeSection.innerHTML = `<h2 class="panel-title">Provocări</h2>`;

  const challengeGrid = document.createElement("div");
  challengeGrid.className = "challenge-grid";
  for (const challenge of challenges) {
    challengeGrid.appendChild(createChallengeCard(challenge));
  }
  challengeSection.appendChild(challengeGrid);
  container.appendChild(challengeSection);

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
