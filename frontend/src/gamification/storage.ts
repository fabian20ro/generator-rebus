/**
 * Local storage layer for all gamification data.
 * Single source of truth for points, stats, and puzzle history.
 */

const STORAGE_KEY = "rebus_player_data";

export interface PuzzleRecord {
  puzzleId: string;
  completedAt: string; // ISO date
  timeSeconds: number;
  difficulty: number;
  gridSize: number;
  hintsUsed: number;
  pointsEarned: number;
  pointsSpent: number; // on hints
}

export interface PlayerData {
  totalPoints: number;
  puzzlesSolved: PuzzleRecord[];
  createdAt: string; // ISO date of first play
}

function defaultPlayerData(): PlayerData {
  return {
    totalPoints: 0,
    puzzlesSolved: [],
    createdAt: new Date().toISOString(),
  };
}

export function loadPlayerData(): PlayerData {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultPlayerData();
    const data = JSON.parse(raw) as PlayerData;
    // Ensure structure integrity
    if (!Array.isArray(data.puzzlesSolved)) data.puzzlesSolved = [];
    if (typeof data.totalPoints !== "number") data.totalPoints = 0;
    if (!data.createdAt) data.createdAt = new Date().toISOString();
    return data;
  } catch {
    return defaultPlayerData();
  }
}

export function savePlayerData(data: PlayerData): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

export function isPuzzleAlreadySolved(puzzleId: string): boolean {
  const data = loadPlayerData();
  return data.puzzlesSolved.some((r) => r.puzzleId === puzzleId);
}

export function recordPuzzleCompletion(record: PuzzleRecord): PlayerData {
  const data = loadPlayerData();
  // Don't double-record
  if (data.puzzlesSolved.some((r) => r.puzzleId === record.puzzleId)) {
    return data;
  }
  data.puzzlesSolved.push(record);
  data.totalPoints += record.pointsEarned - record.pointsSpent;
  savePlayerData(data);
  return data;
}

export function spendPoints(amount: number): boolean {
  const data = loadPlayerData();
  if (data.totalPoints < amount) return false;
  data.totalPoints -= amount;
  savePlayerData(data);
  return true;
}

export function getPoints(): number {
  return loadPlayerData().totalPoints;
}
