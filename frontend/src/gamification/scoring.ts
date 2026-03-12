/**
 * Points calculation engine.
 *
 * Base points scale with difficulty and grid size.
 * Bonuses for speed, no hints, streaks.
 * Hint costs scale with difficulty.
 */

export interface ScoringContext {
  difficulty: number; // 1-5
  gridSize: number; // 7, 10, 15
  timeSeconds: number;
  hintsUsed: number;
}

export interface ScoreBreakdown {
  base: number;
  speedBonus: number;
  noHintBonus: number;
  total: number;
}

/** Base points by difficulty */
const BASE_POINTS: Record<number, number> = {
  1: 50,
  2: 100,
  3: 200,
  4: 350,
  5: 500,
};

/** Grid size multiplier */
function gridMultiplier(size: number): number {
  if (size <= 7) return 1;
  if (size <= 10) return 1.5;
  return 2.5; // 15x15
}

/** Speed bonus thresholds (seconds) */
function speedBonus(timeSeconds: number, difficulty: number): number {
  // Fast threshold scales with difficulty
  const fastThreshold = difficulty * 120; // 2min per difficulty level
  if (timeSeconds <= fastThreshold) {
    return Math.round((fastThreshold - timeSeconds) * 0.5);
  }
  return 0;
}

export function calculateScore(ctx: ScoringContext): ScoreBreakdown {
  const base = Math.round(
    (BASE_POINTS[ctx.difficulty] || 100) * gridMultiplier(ctx.gridSize)
  );
  const speed = speedBonus(ctx.timeSeconds, ctx.difficulty);
  const noHint = ctx.hintsUsed === 0 ? Math.round(base * 0.25) : 0;

  return {
    base,
    speedBonus: speed,
    noHintBonus: noHint,
    total: base + speed + noHint,
  };
}

/** Cost of a letter hint in points */
export function hintLetterCost(difficulty: number): number {
  return 5 + difficulty * 5; // 10, 15, 20, 25, 30
}

/** Cost of a word hint in points */
export function hintWordCost(difficulty: number): number {
  return 20 + difficulty * 15; // 35, 50, 65, 80, 95
}
