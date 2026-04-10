import type {
  Clue,
  PuzzleDetail,
  PuzzleSolution,
  PuzzleSummary,
} from "../types/puzzle";
export type {
  Clue,
  PuzzleDetail,
  PuzzleSolution,
  PuzzleSummary,
} from "../types/puzzle";

/**
 * Puzzle data fetching via Cloudflare Worker proxy.
 */

function normalizeApiBase(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";

  const withProtocol = /^https?:\/\//i.test(trimmed)
    ? trimmed
    : `https://${trimmed}`;

  return withProtocol.replace(/\/+$/, "");
}

// Will be replaced at build time or configured.
// Accept both a full URL and a bare host from CI secrets.
const API_BASE = normalizeApiBase(import.meta.env.VITE_API_BASE || "");

export async function listPuzzles(): Promise<PuzzleSummary[]> {
  const resp = await fetch(`${API_BASE}/puzzles`);
  if (!resp.ok) throw new Error(`Failed to fetch puzzles: ${resp.status}`);
  const puzzles = await resp.json() as PuzzleSummary[];
  return puzzles.sort((a, b) => {
    const aTime = Date.parse(a.repaired_at || a.created_at || "");
    const bTime = Date.parse(b.repaired_at || b.created_at || "");
    return bTime - aTime;
  });
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
