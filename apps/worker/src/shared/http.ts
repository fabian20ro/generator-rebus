import { getCorsHeaders, type Env } from "./cors";

export function jsonResponse(
  data: unknown,
  request: Request,
  env: Env,
  status = 200,
): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      ...getCorsHeaders(request, env),
      "Content-Type": "application/json",
      "Cache-Control": "public, max-age=180",
    },
  });
}
