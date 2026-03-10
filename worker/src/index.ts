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
}

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

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

  // Health check
  if (path === "/health") {
    return jsonResponse({ status: "ok" });
  }

  // List published puzzles
  if (path === "/puzzles") {
    const supabaseUrl = `${env.SUPABASE_URL}/rest/v1/crossword_puzzles?published=eq.true&select=id,title,theme,grid_size,difficulty,created_at&order=created_at.desc`;
    return proxyToSupabase(supabaseUrl, env);
  }

  // Get single puzzle (without solution — for playing)
  const puzzleMatch = path.match(/^\/puzzles\/([a-f0-9-]+)$/);
  if (puzzleMatch) {
    const puzzleId = puzzleMatch[1];

    // Fetch puzzle (template only, no solution)
    const puzzleUrl = `${env.SUPABASE_URL}/rest/v1/crossword_puzzles?id=eq.${puzzleId}&published=eq.true&select=id,title,theme,grid_size,grid_template,difficulty,created_at`;
    const puzzleResp = await fetchFromSupabase(puzzleUrl, env);
    const puzzles = await puzzleResp.json() as any[];

    if (!puzzles || puzzles.length === 0) {
      return jsonResponse({ error: "Puzzle not found" }, 404);
    }

    // Fetch clues
    const cluesUrl = `${env.SUPABASE_URL}/rest/v1/crossword_clues?puzzle_id=eq.${puzzleId}&select=id,direction,start_row,start_col,length,clue_number,definition&order=direction,clue_number`;
    const cluesResp = await fetchFromSupabase(cluesUrl, env);
    const clues = await cluesResp.json();

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
      apikey: env.SUPABASE_ANON_KEY,
      Authorization: `Bearer ${env.SUPABASE_ANON_KEY}`,
    },
  });
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
  fetch: handleRequest,
};
