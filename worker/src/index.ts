/**
 * Cloudflare Worker proxy for Supabase.
 *
 * Routes:
 *   GET /puzzles          → list published puzzles
 *   GET /puzzles/:id      → get a single puzzle with clues
 *   GET /health           → health check
 *
 * The worker adds Supabase auth headers so the frontend
 * never needs to know the Supabase credentials.
 */

interface Env {
  SUPABASE_URL: string;
  SUPABASE_ANON_KEY: string;
  SUPABASE_SERVICE_ROLE_KEY?: string;
}

const CORS_HEADERS = {
  // Keep CORS on every response path so the frontend sees JSON errors too.
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function requireEnv(env: Env): string | null {
  if (!env.SUPABASE_URL) return "Missing SUPABASE_URL";
  if (!env.SUPABASE_ANON_KEY) return "Missing SUPABASE_ANON_KEY";
  return null;
}

function supabaseToken(env: Env): string {
  return env.SUPABASE_SERVICE_ROLE_KEY || env.SUPABASE_ANON_KEY;
}

async function handleRequest(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;

  // CORS preflight
  if (request.method === "OPTIONS") {
    return new Response(null, { headers: CORS_HEADERS });
  }

  if (request.method !== "GET") {
    return jsonResponse({ error: "Method not allowed" }, 405);
  }

  const envError = requireEnv(env);
  if (envError) {
    return jsonResponse({ error: envError }, 500);
  }

  // Health check
  if (path === "/health") {
    return jsonResponse({ status: "ok" });
  }

  // List published puzzles
  if (path === "/puzzles") {
    const supabaseUrl = `${env.SUPABASE_URL}/rest/v1/crossword_puzzles?published=eq.true&select=id,title,theme,description,grid_size,difficulty,pass_rate,created_at,repaired_at&order=repaired_at.desc.nullslast,created_at.desc`;
    return proxyToSupabase(supabaseUrl, env);
  }

  // Get single puzzle (without solution — for playing)
  const puzzleMatch = path.match(/^\/puzzles\/([a-f0-9-]+)$/);
  if (puzzleMatch) {
    const puzzleId = puzzleMatch[1];

    // Fetch puzzle (template only, no solution)
    const puzzleUrl = `${env.SUPABASE_URL}/rest/v1/crossword_puzzles?id=eq.${puzzleId}&published=eq.true&select=id,title,theme,description,grid_size,grid_template,difficulty,created_at,repaired_at`;
    const puzzleResp = await fetchFromSupabase(puzzleUrl, env);
    const puzzles = await puzzleResp.json() as any[];

    if (!puzzles || puzzles.length === 0) {
      return jsonResponse({ error: "Puzzle not found" }, 404);
    }

    const clues = await fetchPuzzleClues(puzzleId, env);

    return jsonResponse({
      puzzle: puzzles[0],
      clues,
    });
  }

  // Get puzzle solution (for checking answers)
  const solutionMatch = path.match(/^\/puzzles\/([a-f0-9-]+)\/solution$/);
  if (solutionMatch) {
    const puzzleId = solutionMatch[1];
    const solutionUrl = `${env.SUPABASE_URL}/rest/v1/crossword_puzzles?id=eq.${puzzleId}&published=eq.true&select=grid_solution`;
    const resp = await fetchFromSupabase(solutionUrl, env);
    const data = await resp.json() as any[];

    if (!data || data.length === 0) {
      return jsonResponse({ error: "Puzzle not found" }, 404);
    }

    return jsonResponse({ solution: data[0].grid_solution });
  }

  return jsonResponse({ error: "Not found" }, 404);
}

async function fetchFromSupabase(url: string, env: Env): Promise<Response> {
  return fetch(url, {
    headers: {
      apikey: supabaseToken(env),
      Authorization: `Bearer ${supabaseToken(env)}`,
    },
  });
}

async function fetchJsonFromSupabase<T>(url: string, env: Env): Promise<T> {
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

async function fetchPuzzleClues(puzzleId: string, env: Env): Promise<any[]> {
  const clueSelects = [
    "id,direction,start_row,start_col,length,clue_number,canonical_definition_id",
    "id,direction,start_row,start_col,length,clue_number,definition",
  ];
  let clues: Array<Record<string, unknown>> = [];
  let lastError: Error | null = null;
  for (const select of clueSelects) {
    try {
      const cluesUrl = `${env.SUPABASE_URL}/rest/v1/crossword_clues?puzzle_id=eq.${puzzleId}&select=${select}&order=direction,clue_number`;
      clues = await fetchJsonFromSupabase<Array<Record<string, unknown>>>(cluesUrl, env);
      lastError = null;
      break;
    } catch (error) {
      lastError = error instanceof Error ? error : new Error("Unknown clue fetch error");
    }
  }
  if (lastError) {
    throw lastError;
  }
  if (!clues.length) {
    return [];
  }

  const canonicalIds = Array.from(
    new Set(
      clues
        .map((clue) => String(clue.canonical_definition_id || ""))
        .filter(Boolean)
    )
  );
  const canonicalById = new Map<string, string>();
  if (canonicalIds.length) {
    const canonicalUrl =
      `${env.SUPABASE_URL}/rest/v1/canonical_clue_definitions?select=id,definition&id=in.(${canonicalIds.join(",")})`;
    try {
      const canonicalRows = await fetchJsonFromSupabase<Array<Record<string, unknown>>>(canonicalUrl, env);
      for (const row of canonicalRows) {
        canonicalById.set(String(row.id || ""), String(row.definition || ""));
      }
    } catch {
      // Fall back to legacy clue definitions if canonical table is unavailable to this token.
    }
  }

  const missingLegacyIds = clues
    .filter((clue) => !canonicalById.has(String(clue.canonical_definition_id || "")) && !clue.definition)
    .map((clue) => String(clue.id || ""))
    .filter(Boolean);
  const legacyById = new Map<string, string>();
  if (missingLegacyIds.length) {
    const legacyUrl =
      `${env.SUPABASE_URL}/rest/v1/crossword_clues?puzzle_id=eq.${puzzleId}&select=id,definition&id=in.(${missingLegacyIds.join(",")})`;
    try {
      const legacyRows = await fetchJsonFromSupabase<Array<Record<string, unknown>>>(legacyUrl, env);
      for (const row of legacyRows) {
        legacyById.set(String(row.id || ""), String(row.definition || ""));
      }
    } catch {
      // If the legacy column is gone, the puzzle should already be fully canonicalized.
    }
  }

  return clues.map((clue) => ({
    id: clue.id,
    direction: clue.direction,
    start_row: clue.start_row,
    start_col: clue.start_col,
    length: clue.length,
    clue_number: clue.clue_number,
    definition:
      canonicalById.get(String(clue.canonical_definition_id || "")) ||
      legacyById.get(String(clue.id || "")) ||
      String(clue.definition || ""),
  }));
}

async function proxyToSupabase(url: string, env: Env): Promise<Response> {
  const resp = await fetchFromSupabase(url, env);
  const body = await resp.text();
  return new Response(body, {
    status: resp.status,
    headers: {
      ...CORS_HEADERS,
      "Content-Type": "application/json",
      "Cache-Control": "public, max-age=180",
    },
  });
}

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      ...CORS_HEADERS,
      "Content-Type": "application/json",
      "Cache-Control": "public, max-age=180",
    },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      return await handleRequest(request, env);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown worker error";
      return jsonResponse({ error: message }, 500);
    }
  },
};
