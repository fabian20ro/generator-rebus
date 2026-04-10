import { routeRequest } from "./app/router";
import { type Env } from "./shared/cors";
import { jsonResponse } from "./shared/http";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      return await routeRequest(request, env);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown worker error";
      return jsonResponse({ error: message }, request, env, 500);
    }
  },
};
