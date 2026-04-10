import type { Env } from "../shared/cors";

export function requireEnv(env: Env): string | null {
  if (!env.SUPABASE_URL) return "Missing SUPABASE_URL";
  if (!env.SUPABASE_ANON_KEY) return "Missing SUPABASE_ANON_KEY";
  return null;
}

function supabaseToken(env: Env): string {
  return env.SUPABASE_SERVICE_ROLE_KEY || env.SUPABASE_ANON_KEY;
}

export async function fetchFromSupabase(url: string, env: Env): Promise<Response> {
  return fetch(url, {
    headers: {
      apikey: supabaseToken(env),
      Authorization: `Bearer ${supabaseToken(env)}`,
    },
  });
}

export async function fetchJsonFromSupabase<T>(url: string, env: Env): Promise<T> {
  const response = await fetchFromSupabase(url, env);
  const data = await response.json() as T;
  if (!response.ok) {
    const message = typeof data === "object" && data && "message" in (data as Record<string, unknown>)
      ? String((data as Record<string, unknown>).message)
      : `Supabase request failed (${response.status})`;
    throw new Error(message);
  }
  return data;
}

export async function fetchPuzzleClues(puzzleId: string, env: Env): Promise<Array<Record<string, unknown>>> {
  const cluesUrl =
    `${env.SUPABASE_URL}/rest/v1/crossword_clue_effective?puzzle_id=eq.${puzzleId}` +
    `&select=id,direction,start_row,start_col,length,clue_number,definition&order=direction,clue_number`;
  return fetchJsonFromSupabase<Array<Record<string, unknown>>>(cluesUrl, env);
}
