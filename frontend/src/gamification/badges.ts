/**
 * Badge / achievement system.
 *
 * Badges are evaluated locally based on PlayerData.
 * Extensible: add new badge definitions to BADGE_DEFINITIONS.
 */

import { STARTING_POINTS, type PlayerData } from "./storage";

export interface Badge {
  id: string;
  name: string;
  description: string;
  icon: string; // emoji or text symbol
  category: "speed" | "milestone" | "special";
}

export interface EarnedBadge extends Badge {
  earnedAt: string; // ISO date
}

/** All badge definitions. Add new ones here. */
export const BADGE_DEFINITIONS: Badge[] = [
  // --- Speed badges ---
  {
    id: "fast_solver",
    name: "Fulger",
    description: "Rezolvă un rebus în mai puțin de 5 minute",
    icon: "\u26A1",
    category: "speed",
  },
  {
    id: "slow_solver",
    name: "R\u0103bd\u0103tor",
    description: "Rezolvă un rebus în mai mult de o săptămână",
    icon: "\uD83D\uDC22",
    category: "speed",
  },

  // --- Milestone badges ---
  {
    id: "solved_1",
    name: "Primul Rebus",
    description: "Rezolvă primul tău rebus",
    icon: "\uD83C\uDFC5",
    category: "milestone",
  },
  {
    id: "solved_2",
    name: "Duo",
    description: "Rezolvă 2 rebusuri",
    icon: "\u270C\uFE0F",
    category: "milestone",
  },
  {
    id: "solved_5",
    name: "Începător",
    description: "Rezolvă 5 rebusuri",
    icon: "\uD83C\uDF1F",
    category: "milestone",
  },
  {
    id: "solved_10",
    name: "Amator",
    description: "Rezolvă 10 rebusuri",
    icon: "\uD83D\uDD25",
    category: "milestone",
  },
  {
    id: "solved_20",
    name: "Pasionat",
    description: "Rezolvă 20 de rebusuri",
    icon: "\uD83D\uDCAA",
    category: "milestone",
  },
  {
    id: "solved_50",
    name: "Maestru",
    description: "Rezolvă 50 de rebusuri",
    icon: "\uD83D\uDC51",
    category: "milestone",
  },
  {
    id: "solved_100",
    name: "Centenar",
    description: "Rezolvă 100 de rebusuri",
    icon: "\uD83D\uDCAF",
    category: "milestone",
  },
  {
    id: "solved_200",
    name: "Bicentenar",
    description: "Rezolvă 200 de rebusuri",
    icon: "\uD83C\uDFC6",
    category: "milestone",
  },
  {
    id: "solved_500",
    name: "Legendă",
    description: "Rezolvă 500 de rebusuri",
    icon: "\uD83C\uDF96\uFE0F",
    category: "milestone",
  },
  {
    id: "solved_1000",
    name: "Mileniul Rebusului",
    description: "Rezolvă 1000 de rebusuri",
    icon: "\uD83C\uDF1E",
    category: "milestone",
  },

  // --- Special badges ---
  {
    id: "no_hints",
    name: "Purist",
    description: "Rezolvă un rebus fără niciun indiciu",
    icon: "\uD83E\uDDD0",
    category: "special",
  },
  {
    id: "hacker",
    name: "Hacker",
    description: "Ai mai multe puncte decât rebusuri rezolvate... suspect!",
    icon: "\uD83D\uDC80",
    category: "special",
  },
];

/** Evaluate which badges the player has earned. */
export function evaluateBadges(data: PlayerData): EarnedBadge[] {
  const earned: EarnedBadge[] = [];
  const solved = data.puzzlesSolved;
  const count = solved.length;

  for (const badge of BADGE_DEFINITIONS) {
    let isEarned = false;
    let earnedAt = "";

    switch (badge.id) {
      // Speed
      case "fast_solver":
        for (const r of solved) {
          if (r.timeSeconds < 300) {
            isEarned = true;
            earnedAt = r.completedAt;
            break;
          }
        }
        break;

      case "slow_solver":
        for (const r of solved) {
          if (r.timeSeconds >= 7 * 24 * 3600) {
            isEarned = true;
            earnedAt = r.completedAt;
            break;
          }
        }
        break;

      // Milestones — data-driven to avoid repetitive cases
      case "solved_1":
      case "solved_2":
      case "solved_5":
      case "solved_10":
      case "solved_20":
      case "solved_50":
      case "solved_100":
      case "solved_200":
      case "solved_500":
      case "solved_1000": {
        const MILESTONES: Record<string, number> = {
          solved_1: 1, solved_2: 2, solved_5: 5,
          solved_10: 10, solved_20: 20, solved_50: 50,
          solved_100: 100, solved_200: 200, solved_500: 500, solved_1000: 1000,
        };
        const target = MILESTONES[badge.id];
        if (target && count >= target) {
          isEarned = true;
          earnedAt = solved[target - 1].completedAt;
        }
        break;
      }

      // Special
      case "no_hints":
        for (const r of solved) {
          if (r.hintsUsed === 0) {
            isEarned = true;
            earnedAt = r.completedAt;
            break;
          }
        }
        break;

      case "hacker":
        // Points higher than starting + max theoretically earnable from puzzles
        if (count > 0 && data.totalPoints > STARTING_POINTS + count * 500 * 2.5) {
          isEarned = true;
          earnedAt = new Date().toISOString();
        }
        break;
    }

    if (isEarned) {
      earned.push({ ...badge, earnedAt });
    }
  }

  return earned;
}

/** Get badges NOT yet earned (for progress display). */
export function getLockedBadges(data: PlayerData): Badge[] {
  const earnedIds = new Set(evaluateBadges(data).map((b) => b.id));
  return BADGE_DEFINITIONS.filter((b) => !earnedIds.has(b.id));
}
