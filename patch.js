const fs = require('fs');
const path = './worker/src/index.ts';
let code = fs.readFileSync(path, 'utf8');

const isOriginAllowedFn = `
function isOriginAllowed(request: Request, env: Env): boolean {
  const origin = request.headers.get("Origin");
  if (!origin) return true; // Allow direct requests without Origin

  const allowedList = env.ALLOWED_ORIGINS
    ? env.ALLOWED_ORIGINS.split(",").map((o) => o.trim())
    : [DEFAULT_ALLOWED_ORIGIN];

  return allowedList.includes(origin);
}
`;

code = code.replace(/function requireEnv/, isOriginAllowedFn + '\nfunction requireEnv');

const preflightReplace = `
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
`;

code = code.replace(/  \/\/ CORS preflight\n  if \(request\.method === "OPTIONS"\) \{\n    return new Response\(null, \{ headers: getCorsHeaders\(request, env\) \}\);\n  \}/, preflightReplace.trim());

fs.writeFileSync(path, code);
