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
  ALLOWED_ORIGINS?: string; // Comma-separated list of origins
}

const DEFAULT_ALLOWED_ORIGIN = "https://fabian20ro.github.io";

function getCorsHeaders(request: Request, env: Env) {
  const origin = request.headers.get("Origin");

  // Default to production domain if not configured
  const allowedList = env.ALLOWED_ORIGINS
    ? env.ALLOWED_ORIGINS.split(",").map((o) => o.trim())
    : [DEFAULT_ALLOWED_ORIGIN];

  const isAllowed = origin && allowedList.includes(origin);

  return {
    "Access-Control-Allow-Origin": isAllowed ? origin : "",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
  };
}


function isOriginAllowed(request: Request, env: Env): boolean {
  const origin = request.headers.get("Origin");
  if (!origin) return true; // Allow direct requests without Origin

  const allowedList = env.ALLOWED_ORIGINS
    ? env.ALLOWED_ORIGINS.split(",").map((o) => o.trim())
    : [DEFAULT_ALLOWED_ORIGIN];

  return allowedList.includes(origin);
}

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
    if (!isOriginAllowed(request, env)) {
      return new Response(null, { status: 403 });
    }
    return new Response(null, { headers: getCorsHeaders(request, env) });
  }

  if (!isOriginAllowed(request, env)) {
    return jsonResponse({ error: "Forbidden" }, request, env, 403);
  }

  if (request.method !== "GET") {
    return jsonResponse({ error: "Method not allowed" }, request, env, 405);
  }

  const envError = requireEnv(env);
  if (envError) {
    return jsonResponse({ error: envError }, request, env, 500);
  }

  // Health check
  if (path === "/health") {
    return jsonResponse({ status: "ok" }, request, env);
  }

  // List published puzzles
  if (path === "/puzzles") {
    const supabaseUrl = `${env.SUPABASE_URL}/rest/v1/crossword_puzzles?published=eq.true&select=id,title,theme,description,grid_size,difficulty,pass_rate,created_at,repaired_at&order=repaired_at.desc.nullslast,created_at.desc`;
    return proxyToSupabase(supabaseUrl, env, request);
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
      return jsonResponse({ error: "Puzzle not found" }, request, env, 404);
    }

    const clues = await fetchPuzzleClues(puzzleId, env);

    return jsonResponse({
      puzzle: puzzles[0],
      clues,
    }, request, env);
  }

  // Get puzzle solution (for checking answers)
  const solutionMatch = path.match(/^\/puzzles\/([a-f0-9-]+)\/solution$/);
  if (solutionMatch) {
    const puzzleId = solutionMatch[1];
    const solutionUrl = `${env.SUPABASE_URL}/rest/v1/crossword_puzzles?id=eq.${puzzleId}&published=eq.true&select=grid_solution`;
    const resp = await fetchFromSupabase(solutionUrl, env);
    const data = await resp.json() as any[];

    if (!data || data.length === 0) {
      return jsonResponse({ error: "Puzzle not found" }, request, env, 404);
    }

    return jsonResponse({ solution: data[0].grid_solution }, request, env);
  }

  return jsonResponse({ error: "Not found" }, request, env, 404);
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
  const cluesUrl =
    `${env.SUPABASE_URL}/rest/v1/crossword_clue_effective?puzzle_id=eq.${puzzleId}` +
    `&select=id,direction,start_row,start_col,length,clue_number,definition&order=direction,clue_number`;
  const clues = await fetchJsonFromSupabase<Array<Record<string, unknown>>>(cluesUrl, env);
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
  const resp = await fetchFromSupabase(url, env);
  const body = await resp.text();
  return new Response(body, {
    status: resp.status,
    headers: {
      ...getCorsHeaders(request, env),
      "Content-Type": "application/json",
      "Cache-Control": "public, max-age=180",
    },
  });
}

function jsonResponse(data: unknown, request: Request, env: Env, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      ...getCorsHeaders(request, env),
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
      return jsonResponse({ error: message }, request, env, 500);
    }
  },
};
