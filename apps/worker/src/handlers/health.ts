import type { Env } from "../shared/cors";
import { jsonResponse } from "../shared/http";

export function handleHealth(request: Request, env: Env): Response {
  return jsonResponse({ status: "ok" }, request, env);
}
