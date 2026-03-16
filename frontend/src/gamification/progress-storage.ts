/**
 * Save/load/clear puzzle progress in localStorage.
 * Each puzzle gets its own key to avoid loading all puzzles on every save.
 */

export interface PuzzleProgress {
  cells: (string | null)[][];
  revealed?: boolean[][];
  pencilCells?: boolean[][];
  hintsUsed: number;
  elapsedSeconds: number;
  savedAt: string;
}

function storageKey(puzzleId: string): string {
  return `rebus_progress_${puzzleId}`;
}

export function saveProgress(
  puzzleId: string,
  progress: PuzzleProgress
): void {
  try {
    localStorage.setItem(storageKey(puzzleId), JSON.stringify(progress));
  } catch {
    // localStorage full or unavailable — silently skip
  }
}

export function loadProgress(puzzleId: string): PuzzleProgress | null {
  try {
    const raw = localStorage.getItem(storageKey(puzzleId));
    if (!raw) return null;
    const data = JSON.parse(raw) as PuzzleProgress;
    if (!Array.isArray(data.cells) || typeof data.elapsedSeconds !== "number" ||
        typeof data.hintsUsed !== "number") {
      return null;
    }
    return data;
  } catch {
    return null;
  }
}

export function clearProgress(puzzleId: string): void {
  try {
    localStorage.removeItem(storageKey(puzzleId));
  } catch {
    // localStorage unavailable — silently skip
  }
}

export function hasProgress(puzzleId: string): boolean {
  try {
    return localStorage.getItem(storageKey(puzzleId)) !== null;
  } catch {
    return false;
  }
}
