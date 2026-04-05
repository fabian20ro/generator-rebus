import { calculateScore, hintLetterCost, hintWordCost } from './scoring';

describe('Scoring Logic', () => {
  describe('calculateScore', () => {
    test('calculates base score correctly for different difficulties', () => {
      // Difficulty 1, Grid 7: 50 * 1 = 50
      expect(calculateScore({ difficulty: 1, gridSize: 7, timeSeconds: 9999, hintsUsed: 1 }).base).toBe(50);
      // Difficulty 3, Grid 7: 200 * 1 = 200
      expect(calculateScore({ difficulty: 3, gridSize: 7, timeSeconds: 9999, hintsUsed: 1 }).base).toBe(200);
      // Difficulty 5, Grid 7: 500 * 1 = 500
      expect(calculateScore({ difficulty: 5, gridSize: 7, timeSeconds: 9999, hintsUsed: 1 }).base).toBe(500);
      // Default difficulty: 100 * 1 = 100
      expect(calculateScore({ difficulty: 10, gridSize: 7, timeSeconds: 9999, hintsUsed: 1 }).base).toBe(100);
    });

    test('applies grid size multiplier correctly', () => {
      // Grid 7: multiplier 1 -> 100 * 1 = 100
      expect(calculateScore({ difficulty: 2, gridSize: 7, timeSeconds: 9999, hintsUsed: 1 }).base).toBe(100);
      // Grid 10: multiplier 1.5 -> 100 * 1.5 = 150
      expect(calculateScore({ difficulty: 2, gridSize: 10, timeSeconds: 9999, hintsUsed: 1 }).base).toBe(150);
      // Grid 15: multiplier 2.5 -> 100 * 2.5 = 250
      expect(calculateScore({ difficulty: 2, gridSize: 15, timeSeconds: 9999, hintsUsed: 1 }).base).toBe(250);
    });

    test('calculates speed bonus correctly', () => {
      // Difficulty 1: threshold 120s. 60s used. Bonus: (120 - 60) * 0.5 = 30
      expect(calculateScore({ difficulty: 1, gridSize: 7, timeSeconds: 60, hintsUsed: 1 }).speedBonus).toBe(30);
      // Difficulty 2: threshold 240s. 100s used. Bonus: (240 - 100) * 0.5 = 70
      expect(calculateScore({ difficulty: 2, gridSize: 7, timeSeconds: 100, hintsUsed: 1 }).speedBonus).toBe(70);
      // No bonus if over threshold
      expect(calculateScore({ difficulty: 1, gridSize: 7, timeSeconds: 121, hintsUsed: 1 }).speedBonus).toBe(0);
    });

    test('applies no-hint bonus correctly', () => {
      // Base 100, no hints: 100 * 0.25 = 25
      expect(calculateScore({ difficulty: 2, gridSize: 7, timeSeconds: 9999, hintsUsed: 0 }).noHintBonus).toBe(25);
      // Base 100, hints used: 0
      expect(calculateScore({ difficulty: 2, gridSize: 7, timeSeconds: 9999, hintsUsed: 1 }).noHintBonus).toBe(0);
    });

    test('calculates total score correctly', () => {
      // Diff 2 (100), Grid 10 (x1.5) = 150 base
      // Time 140s (threshold 240): (240-140)*0.5 = 50 speed
      // No hints: 150 * 0.25 = 37.5 -> Math.round(37.5) = 38
      // Total: 150 + 50 + 38 = 238
      const result = calculateScore({ difficulty: 2, gridSize: 10, timeSeconds: 140, hintsUsed: 0 });
      expect(result.base).toBe(150);
      expect(result.speedBonus).toBe(50);
      expect(result.noHintBonus).toBe(38);
      expect(result.total).toBe(238);
    });
  });

  describe('hint costs', () => {
    test('hintLetterCost scales with difficulty', () => {
      expect(hintLetterCost(1)).toBe(10);
      expect(hintLetterCost(3)).toBe(20);
      expect(hintLetterCost(5)).toBe(30);
    });

    test('hintWordCost scales with difficulty', () => {
      expect(hintWordCost(1)).toBe(35);
      expect(hintWordCost(3)).toBe(65);
      expect(hintWordCost(5)).toBe(95);
    });
  });
});
