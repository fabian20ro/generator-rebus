import { handleHealth } from "../handlers/health";
import {
  handlePuzzleDetail,
  handlePuzzleList,
  handlePuzzleSolution,
} from "../handlers/puzzles";
import { requireEnv } from "../infra/supabase";
import { getCorsHeaders, isOriginAllowed, type Env } from "../shared/cors";
import { jsonResponse } from "../shared/http";

export async function routeRequest(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;

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

  if (path === "/health") {
    return handleHealth(request, env);
  }

  if (path === "/puzzles") {
    return handlePuzzleList(request, env);
  }

  const puzzleMatch = path.match(/^\/puzzles\/([a-f0-9-]+)$/);
  if (puzzleMatch) {
    return handlePuzzleDetail(request, env, puzzleMatch[1]);
  }

  const solutionMatch = path.match(/^\/puzzles\/([a-f0-9-]+)\/solution$/);
  if (solutionMatch) {
    return handlePuzzleSolution(request, env, solutionMatch[1]);
  }

  return jsonResponse({ error: "Not found" }, request, env, 404);
}
