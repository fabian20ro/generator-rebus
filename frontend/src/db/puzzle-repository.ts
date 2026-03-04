/**
 * Puzzle data fetching via Cloudflare Worker proxy.
 */

// Will be replaced at build time or configured
const API_BASE = import.meta.env.VITE_API_BASE || "";

export interface PuzzleSummary {
  id: string;
  title: string;
  theme: string;
  grid_size: number;
  difficulty: number;
  created_at: string;
}

export interface Clue {
  id: string;
  direction: "H" | "V";
  start_row: number;
  start_col: number;
  length: number;
  clue_number: number;
  definition: string;
}

export interface PuzzleDetail {
  puzzle: {
    id: string;
    title: string;
    theme: string;
    grid_size: number;
    grid_template: string; // JSON string: boolean[][]
    difficulty: number;
    created_at: string;
  };
  clues: Clue[];
}

export interface PuzzleSolution {
  solution: string; // JSON string: (string | null)[][]
}

export async function listPuzzles(): Promise<PuzzleSummary[]> {
  const resp = await fetch(`${API_BASE}/puzzles`);
  if (!resp.ok) throw new Error(`Failed to fetch puzzles: ${resp.status}`);
  return resp.json();
}

export async function getPuzzle(id: string): Promise<PuzzleDetail> {
  const resp = await fetch(`${API_BASE}/puzzles/${id}`);
  if (!resp.ok) throw new Error(`Failed to fetch puzzle: ${resp.status}`);
  return resp.json();
}

export async function getSolution(id: string): Promise<PuzzleSolution> {
  const resp = await fetch(`${API_BASE}/puzzles/${id}/solution`);
  if (!resp.ok) throw new Error(`Failed to fetch solution: ${resp.status}`);
  return resp.json();
}
