import { evaluateBadges, BADGE_DEFINITIONS } from './badges';
import type { PlayerData, PuzzleRecord } from './storage';

describe('Badge System', () => {
  const mockRecord = (overrides: Partial<PuzzleRecord> = {}): PuzzleRecord => ({
    puzzleId: 'p1',
    completedAt: '2026-04-13T12:00:00Z',
    timeSeconds: 600,
    difficulty: 1,
    gridSize: 10,
    hintsUsed: 0,
    checksUsed: 0,
    backspacesUsed: 0,
    pointsEarned: 100,
    pointsSpent: 0,
    ...overrides,
  });

  const mockPlayerData = (solved: PuzzleRecord[] = []): PlayerData => ({
    totalPoints: 500,
    puzzlesSolved: solved,
    createdAt: '2026-04-13T10:00:00Z',
  });

  test('no_hints badge (Purist)', () => {
    const data = mockPlayerData([mockRecord({ hintsUsed: 0 })]);
    const earned = evaluateBadges(data);
    expect(earned.some(b => b.id === 'no_hints')).toBe(true);

    const dataWithHints = mockPlayerData([mockRecord({ hintsUsed: 1 })]);
    expect(evaluateBadges(dataWithHints).some(b => b.id === 'no_hints')).toBe(false);
  });

  test('no_checks badge (Fără verificări)', () => {
    const data = mockPlayerData([mockRecord({ checksUsed: 0 })]);
    const earned = evaluateBadges(data);
    expect(earned.some(b => b.id === 'no_checks')).toBe(true);

    const dataWithChecks = mockPlayerData([mockRecord({ checksUsed: 1 })]);
    expect(evaluateBadges(dataWithChecks).some(b => b.id === 'no_checks')).toBe(false);
  });

  test('century_20 badge (Ca-n secolul 20)', () => {
    const data = mockPlayerData([mockRecord({ hintsUsed: 0, checksUsed: 0 })]);
    const earned = evaluateBadges(data);
    expect(earned.some(b => b.id === 'century_20')).toBe(true);

    const dataHybrid = mockPlayerData([mockRecord({ hintsUsed: 1, checksUsed: 0 })]);
    expect(evaluateBadges(dataHybrid).some(b => b.id === 'century_20')).toBe(false);
  });

  test('zen_master badge (Maestru Zen)', () => {
    const data = mockPlayerData([mockRecord({ backspacesUsed: 0 })]);
    const earned = evaluateBadges(data);
    expect(earned.some(b => b.id === 'zen_master')).toBe(true);

    const dataWithBackspaces = mockPlayerData([mockRecord({ backspacesUsed: 1 })]);
    expect(evaluateBadges(dataWithBackspaces).some(b => b.id === 'zen_master')).toBe(false);

    // Legacy record (backspacesUsed = -1) should not earn Zen Master
    const dataLegacy = mockPlayerData([mockRecord({ backspacesUsed: -1 })]);
    expect(evaluateBadges(dataLegacy).some(b => b.id === 'zen_master')).toBe(false);
  });

  test('no_sleep and morning badges', () => {
    // We use a helper to get consistent hours across timezones in CI
    const getNightISO = () => {
      const d = new Date();
      d.setHours(2, 0, 0, 0); // 2 AM
      return d.toISOString();
    };
    const getMorningISO = () => {
      const d = new Date();
      d.setHours(7, 0, 0, 0); // 7 AM
      return d.toISOString();
    };

    const nightData = mockPlayerData([mockRecord({ completedAt: getNightISO() })]);
    expect(evaluateBadges(nightData).some(b => b.id === 'no_sleep')).toBe(true);

    const morningData = mockPlayerData([mockRecord({ completedAt: getMorningISO() })]);
    expect(evaluateBadges(morningData).some(b => b.id === 'morning')).toBe(true);
  });

  test('duminica_in_familie badge', () => {
    // Find a real Sunday date to avoid timezone issues with hardcoded UTC
    const sunday = new Date();
    while (sunday.getDay() !== 0) {
      sunday.setDate(sunday.getDate() + 1);
    }
    const sundayISO = sunday.toISOString();

    const dataCorrect = mockPlayerData([
      mockRecord({ puzzleId: '1', gridSize: 13, completedAt: sundayISO }),
      mockRecord({ puzzleId: '2', gridSize: 14, completedAt: sundayISO }),
    ]);
    expect(evaluateBadges(dataCorrect).some(b => b.id === 'duminica_in_familie')).toBe(true);
  });

  test('sprint badge', () => {
    const data = mockPlayerData([
      mockRecord({ puzzleId: '1', gridSize: 7, timeSeconds: 150 }),
      mockRecord({ puzzleId: '2', gridSize: 8, timeSeconds: 120 }),
      mockRecord({ puzzleId: '3', gridSize: 9, timeSeconds: 179 }),
    ]);
    expect(evaluateBadges(data).some(b => b.id === 'sprint')).toBe(true);

    const dataSlow = mockPlayerData([
      mockRecord({ puzzleId: '1', gridSize: 7, timeSeconds: 150 }),
      mockRecord({ puzzleId: '2', gridSize: 8, timeSeconds: 120 }),
      mockRecord({ puzzleId: '3', gridSize: 9, timeSeconds: 181 }),
    ]);
    expect(evaluateBadges(dataSlow).some(b => b.id === 'sprint')).toBe(false);
  });

  test('size milestones', () => {
    const data = mockPlayerData([
      mockRecord({ puzzleId: '1', gridSize: 7 }),
      mockRecord({ puzzleId: '2', gridSize: 7 }),
      mockRecord({ puzzleId: '3', gridSize: 7 }),
      mockRecord({ puzzleId: '4', gridSize: 7 }),
      mockRecord({ puzzleId: '5', gridSize: 7 }),
    ]);
    const earned = evaluateBadges(data);
    expect(earned.some(b => b.id === 'size_7_1')).toBe(true);
    expect(earned.some(b => b.id === 'size_7_5')).toBe(true);
    expect(earned.some(b => b.id === 'size_7_10')).toBe(false);
    expect(earned.some(b => b.id === 'size_8_1')).toBe(false);
  });

  test('size_trio badge exists', () => {
    const sizeTrioBadge = BADGE_DEFINITIONS.find(b => b.id === 'size_trio');
    expect(sizeTrioBadge).toBeDefined();
    expect(sizeTrioBadge?.id).toBe('size_trio');
  });

  test('size_trio badge evaluation', () => {
    const rSmall = mockRecord({ puzzleId: 'small', gridSize: 9, completedAt: '2024-01-01T12:00:00Z' });
    const rMedium = mockRecord({ puzzleId: 'medium', gridSize: 11, completedAt: '2025-01-01T12:00:00Z' });
    const rLarge = mockRecord({ puzzleId: 'large', gridSize: 13, completedAt: '2026-01-01T12:00:00Z' });
    
    const data = mockPlayerData([rSmall, rMedium, rLarge]);
    const earned = evaluateBadges(data);
    
    expect(earned.some(b => b.id === 'size_trio')).toBe(true);
  });

  test('removed solved_1000 badge', () => {
    expect(BADGE_DEFINITIONS.some(b => b.id === 'solved_1000')).toBe(false);
  });
});
