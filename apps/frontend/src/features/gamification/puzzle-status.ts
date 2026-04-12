import type { PuzzleSummary } from "../../shared/types/puzzle";
import type { PuzzleProgress } from "./progress-storage";
import type { PlayerData, PuzzleRecord } from "./storage";

export const PUZZLE_SIZE_OPTIONS = [7, 8, 9, 10, 11, 12, 13, 14, 15] as const;

export type ExactPuzzleSize = (typeof PUZZLE_SIZE_OPTIONS)[number];
export type AvailableSizeFilter = "all" | ExactPuzzleSize;
export type AppTab =
  | "available"
  | "in_progress"
  | "solved"
  | "statistics"
  | "rewards";
export type PuzzleDerivedStatus = "available" | "in_progress" | "solved";

export interface AvailableTabBrowseState {
  size: AvailableSizeFilter;
}

export interface TabConfig {
  id: AppTab;
  icon: string;
  label: string;
  visible: boolean;
  count?: number;
}

export interface PuzzleTabItem extends PuzzleSummary {
  localStatus: PuzzleDerivedStatus;
  savedAt: string | null;
  solvedAt: string | null;
  solvedRecord: PuzzleRecord | null;
}

export interface DerivedPuzzleState {
  all: PuzzleTabItem[];
  available: PuzzleTabItem[];
  inProgress: PuzzleTabItem[];
  solved: PuzzleTabItem[];
  visibleAvailable: PuzzleTabItem[];
  solvedCountBySize: Record<ExactPuzzleSize, number>;
  statisticsVisible: boolean;
}

function getRecencyTimestamp(puzzle: PuzzleSummary): number {
  const raw = puzzle.repaired_at || puzzle.created_at || "";
  const timestamp = Date.parse(raw);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function getSavedTimestamp(value: string | null): number {
  if (!value) return 0;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function getSolvedTimestamp(record: PuzzleRecord | null): number {
  if (!record) return 0;
  const timestamp = Date.parse(record.completedAt);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function rankByPassRate(items: PuzzleTabItem[]): PuzzleTabItem[] {
  return [...items].sort((a, b) => {
    const aRate = a.pass_rate ?? -1;
    const bRate = b.pass_rate ?? -1;
    if (aRate !== bRate) {
      return bRate - aRate;
    }
    return getRecencyTimestamp(b) - getRecencyTimestamp(a);
  });
}

function sortByRecency(items: PuzzleTabItem[]): PuzzleTabItem[] {
  return [...items].sort((a, b) => getRecencyTimestamp(b) - getRecencyTimestamp(a));
}

function sortInProgress(items: PuzzleTabItem[]): PuzzleTabItem[] {
  return [...items].sort((a, b) => {
    const savedDelta = getSavedTimestamp(b.savedAt) - getSavedTimestamp(a.savedAt);
    if (savedDelta !== 0) {
      return savedDelta;
    }
    return getRecencyTimestamp(b) - getRecencyTimestamp(a);
  });
}

function sortSolved(items: PuzzleTabItem[]): PuzzleTabItem[] {
  return [...items].sort((a, b) => {
    const solvedDelta = getSolvedTimestamp(b.solvedRecord) - getSolvedTimestamp(a.solvedRecord);
    if (solvedDelta !== 0) {
      return solvedDelta;
    }
    return getRecencyTimestamp(b) - getRecencyTimestamp(a);
  });
}

function buildSolvedCountBySize(data: PlayerData): Record<ExactPuzzleSize, number> {
  const counts = Object.fromEntries(
    PUZZLE_SIZE_OPTIONS.map((size) => [size, 0])
  ) as Record<ExactPuzzleSize, number>;

  for (const record of data.puzzlesSolved) {
    if (record.gridSize in counts) {
      counts[record.gridSize as ExactPuzzleSize] += 1;
    }
  }

  return counts;
}

function selectOrganicAvailable(
  items: PuzzleTabItem[],
  solvedCountBySize: Record<ExactPuzzleSize, number>
): PuzzleTabItem[] {
  const selected: PuzzleTabItem[] = [];

  for (const size of PUZZLE_SIZE_OPTIONS) {
    const sameSize = items.filter((item) => item.grid_size === size);
    if (sameSize.length === 0) {
      continue;
    }

    const unlockedCount = 3 * (1 + solvedCountBySize[size]);
    const ranked = rankByPassRate(sameSize.filter((item) => item.pass_rate !== null && item.pass_rate !== undefined));
    const fallback = sortByRecency(sameSize.filter((item) => item.pass_rate === null || item.pass_rate === undefined));
    selected.push(...ranked.slice(0, unlockedCount));

    if (ranked.length < unlockedCount) {
      selected.push(...fallback.slice(0, unlockedCount - ranked.length));
    }
  }

  return sortByRecency(selected);
}

export function filterAvailableBySize(
  items: PuzzleTabItem[],
  size: AvailableSizeFilter
): PuzzleTabItem[] {
  if (size === "all") {
    return items;
  }
  return items.filter((item) => item.grid_size === size);
}

export function derivePuzzleState(
  puzzles: PuzzleSummary[],
  data: PlayerData,
  progressById: Map<string, PuzzleProgress>
): DerivedPuzzleState {
  const solvedById = new Map<string, PuzzleRecord>(
    data.puzzlesSolved.map((record) => [record.puzzleId, record])
  );

  const all = puzzles.map((puzzle) => {
    const solvedRecord = solvedById.get(puzzle.id) ?? null;
    const progress = solvedRecord ? null : (progressById.get(puzzle.id) ?? null);
    const localStatus: PuzzleDerivedStatus = solvedRecord
      ? "solved"
      : progress
        ? "in_progress"
        : "available";

    return {
      ...puzzle,
      localStatus,
      savedAt: progress?.savedAt ?? null,
      solvedAt: solvedRecord?.completedAt ?? null,
      solvedRecord,
    };
  });

  const solvedCountBySize = buildSolvedCountBySize(data);
  const available = all.filter((item) => item.localStatus === "available");
  const inProgress = sortInProgress(all.filter((item) => item.localStatus === "in_progress"));
  const solved = sortSolved(all.filter((item) => item.localStatus === "solved"));

  return {
    all,
    available,
    inProgress,
    solved,
    visibleAvailable: selectOrganicAvailable(available, solvedCountBySize),
    solvedCountBySize,
    statisticsVisible: inProgress.length > 0 || solved.length > 0,
  };
}

export function buildTabConfig(state: DerivedPuzzleState): TabConfig[] {
  return [
    {
      id: "available",
      icon: "🧩",
      label: "Disponibile",
      visible: true,
    },
    {
      id: "in_progress",
      icon: "⏳",
      label: "În curs",
      visible: state.inProgress.length > 0,
      count: state.inProgress.length,
    },
    {
      id: "solved",
      icon: "✅",
      label: "Rezolvate",
      visible: state.solved.length > 0,
      count: state.solved.length,
    },
    {
      id: "statistics",
      icon: "📊",
      label: "Statistici",
      visible: state.statisticsVisible,
    },
    {
      id: "rewards",
      icon: "🏆",
      label: "Insigne",
      visible: true,
    },
  ];
}
