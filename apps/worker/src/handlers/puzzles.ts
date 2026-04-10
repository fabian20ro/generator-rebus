import { getCorsHeaders, type Env } from "../shared/cors";
import { jsonResponse } from "../shared/http";
import { fetchFromSupabase, fetchPuzzleClues } from "../infra/supabase";

function normalizeClues(clues: Array<Record<string, unknown>>) {
  return clues.map((clue) => ({
    id: clue.id,
    direction: clue.direction,
    start_row: clue.start_row,
    start_col: clue.start_col,
    length: clue.length,
    clue_number: clue.clue_number,
    definition: String(clue.definition || ""),
  }));
}

async function proxyToSupabase(url: string, env: Env, request: Request): Promise<Response> {
  const response = await fetchFromSupabase(url, env);
  const body = await response.text();
  return new Response(body, {
    status: response.status,
    headers: {
      ...getCorsHeaders(request, env),
      "Content-Type": "application/json",
      "Cache-Control": "public, max-age=180",
    },
  });
}

export async function handlePuzzleList(request: Request, env: Env): Promise<Response> {
  const url = `${env.SUPABASE_URL}/rest/v1/crossword_puzzles?published=eq.true&select=id,title,description,grid_size,difficulty,pass_rate,created_at,repaired_at&order=repaired_at.desc.nullslast,created_at.desc`;
  return proxyToSupabase(url, env, request);
}

export async function handlePuzzleDetail(
  request: Request,
  env: Env,
  puzzleId: string,
): Promise<Response> {
  const puzzleUrl = `${env.SUPABASE_URL}/rest/v1/crossword_puzzles?id=eq.${puzzleId}&published=eq.true&select=id,title,description,grid_size,grid_template,difficulty,created_at,repaired_at`;
  const response = await fetchFromSupabase(puzzleUrl, env);
  const puzzles = await response.json() as any[];

  if (!puzzles || puzzles.length === 0) {
    return jsonResponse({ error: "Puzzle not found" }, request, env, 404);
  }

  const clues = await fetchPuzzleClues(puzzleId, env);
  return jsonResponse({ puzzle: puzzles[0], clues: normalizeClues(clues) }, request, env);
}

export async function handlePuzzleSolution(
  request: Request,
  env: Env,
  puzzleId: string,
): Promise<Response> {
  const solutionUrl = `${env.SUPABASE_URL}/rest/v1/crossword_puzzles?id=eq.${puzzleId}&published=eq.true&select=grid_solution`;
  const response = await fetchFromSupabase(solutionUrl, env);
  const data = await response.json() as any[];

  if (!data || data.length === 0) {
    return jsonResponse({ error: "Puzzle not found" }, request, env, 404);
  }

  return jsonResponse({ solution: data[0].grid_solution }, request, env);
}
