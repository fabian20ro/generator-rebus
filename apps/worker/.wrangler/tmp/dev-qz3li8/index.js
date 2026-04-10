var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// .wrangler/tmp/bundle-oCeNxE/checked-fetch.js
var urls = /* @__PURE__ */ new Set();
function checkURL(request, init) {
  const url = request instanceof URL ? request : new URL(
    (typeof request === "string" ? new Request(request, init) : request).url
  );
  if (url.port && url.port !== "443" && url.protocol === "https:") {
    if (!urls.has(url.toString())) {
      urls.add(url.toString());
      console.warn(
        `WARNING: known issue with \`fetch()\` requests to custom HTTPS ports in published Workers:
 - ${url.toString()} - the custom port will be ignored when the Worker is published using the \`wrangler deploy\` command.
`
      );
    }
  }
}
__name(checkURL, "checkURL");
globalThis.fetch = new Proxy(globalThis.fetch, {
  apply(target, thisArg, argArray) {
    const [request, init] = argArray;
    checkURL(request, init);
    return Reflect.apply(target, thisArg, argArray);
  }
});

// .wrangler/tmp/bundle-oCeNxE/strip-cf-connecting-ip-header.js
function stripCfConnectingIPHeader(input, init) {
  const request = new Request(input, init);
  request.headers.delete("CF-Connecting-IP");
  return request;
}
__name(stripCfConnectingIPHeader, "stripCfConnectingIPHeader");
globalThis.fetch = new Proxy(globalThis.fetch, {
  apply(target, thisArg, argArray) {
    return Reflect.apply(target, thisArg, [
      stripCfConnectingIPHeader.apply(null, argArray)
    ]);
  }
});

// src/index.ts
var DEFAULT_ALLOWED_ORIGIN = "https://fabian20ro.github.io";
function getCorsHeaders(request, env) {
  const origin = request.headers.get("Origin");
  const allowedList = env.ALLOWED_ORIGINS ? env.ALLOWED_ORIGINS.split(",").map((o) => o.trim()) : [DEFAULT_ALLOWED_ORIGIN];
  const isAllowed = origin && allowedList.includes(origin);
  return {
    "Access-Control-Allow-Origin": isAllowed ? origin : "",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin"
  };
}
__name(getCorsHeaders, "getCorsHeaders");
function isOriginAllowed(request, env) {
  const origin = request.headers.get("Origin");
  if (!origin)
    return true;
  const allowedList = env.ALLOWED_ORIGINS ? env.ALLOWED_ORIGINS.split(",").map((o) => o.trim()) : [DEFAULT_ALLOWED_ORIGIN];
  return allowedList.includes(origin);
}
__name(isOriginAllowed, "isOriginAllowed");
function requireEnv(env) {
  if (!env.SUPABASE_URL)
    return "Missing SUPABASE_URL";
  if (!env.SUPABASE_ANON_KEY)
    return "Missing SUPABASE_ANON_KEY";
  return null;
}
__name(requireEnv, "requireEnv");
function supabaseToken(env) {
  return env.SUPABASE_SERVICE_ROLE_KEY || env.SUPABASE_ANON_KEY;
}
__name(supabaseToken, "supabaseToken");
async function handleRequest(request, env) {
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
    return jsonResponse({ status: "ok" }, request, env);
  }
  if (path === "/puzzles") {
    const supabaseUrl = `${env.SUPABASE_URL}/rest/v1/crossword_puzzles?published=eq.true&select=id,title,theme,description,grid_size,difficulty,pass_rate,created_at,repaired_at&order=repaired_at.desc.nullslast,created_at.desc`;
    return proxyToSupabase(supabaseUrl, env, request);
  }
  const puzzleMatch = path.match(/^\/puzzles\/([a-f0-9-]+)$/);
  if (puzzleMatch) {
    const puzzleId = puzzleMatch[1];
    const puzzleUrl = `${env.SUPABASE_URL}/rest/v1/crossword_puzzles?id=eq.${puzzleId}&published=eq.true&select=id,title,theme,description,grid_size,grid_template,difficulty,created_at,repaired_at`;
    const puzzleResp = await fetchFromSupabase(puzzleUrl, env);
    const puzzles = await puzzleResp.json();
    if (!puzzles || puzzles.length === 0) {
      return jsonResponse({ error: "Puzzle not found" }, request, env, 404);
    }
    const clues = await fetchPuzzleClues(puzzleId, env);
    return jsonResponse({
      puzzle: puzzles[0],
      clues
    }, request, env);
  }
  const solutionMatch = path.match(/^\/puzzles\/([a-f0-9-]+)\/solution$/);
  if (solutionMatch) {
    const puzzleId = solutionMatch[1];
    const solutionUrl = `${env.SUPABASE_URL}/rest/v1/crossword_puzzles?id=eq.${puzzleId}&published=eq.true&select=grid_solution`;
    const resp = await fetchFromSupabase(solutionUrl, env);
    const data = await resp.json();
    if (!data || data.length === 0) {
      return jsonResponse({ error: "Puzzle not found" }, request, env, 404);
    }
    return jsonResponse({ solution: data[0].grid_solution }, request, env);
  }
  return jsonResponse({ error: "Not found" }, request, env, 404);
}
__name(handleRequest, "handleRequest");
async function fetchFromSupabase(url, env) {
  return fetch(url, {
    headers: {
      apikey: supabaseToken(env),
      Authorization: `Bearer ${supabaseToken(env)}`
    }
  });
}
__name(fetchFromSupabase, "fetchFromSupabase");
async function fetchJsonFromSupabase(url, env) {
  const response = await fetchFromSupabase(url, env);
  const data = await response.json();
  if (!response.ok) {
    const message = typeof data === "object" && data && "message" in data ? String(data.message) : `Supabase request failed (${response.status})`;
    throw new Error(message);
  }
  return data;
}
__name(fetchJsonFromSupabase, "fetchJsonFromSupabase");
async function fetchPuzzleClues(puzzleId, env) {
  const cluesUrl = `${env.SUPABASE_URL}/rest/v1/crossword_clue_effective?puzzle_id=eq.${puzzleId}&select=id,direction,start_row,start_col,length,clue_number,definition&order=direction,clue_number`;
  const clues = await fetchJsonFromSupabase(cluesUrl, env);
  return clues.map((clue) => ({
    id: clue.id,
    direction: clue.direction,
    start_row: clue.start_row,
    start_col: clue.start_col,
    length: clue.length,
    clue_number: clue.clue_number,
    definition: String(clue.definition || "")
  }));
}
__name(fetchPuzzleClues, "fetchPuzzleClues");
async function proxyToSupabase(url, env, request) {
  const resp = await fetchFromSupabase(url, env);
  const body = await resp.text();
  return new Response(body, {
    status: resp.status,
    headers: {
      ...getCorsHeaders(request, env),
      "Content-Type": "application/json",
      "Cache-Control": "public, max-age=180"
    }
  });
}
__name(proxyToSupabase, "proxyToSupabase");
function jsonResponse(data, request, env, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      ...getCorsHeaders(request, env),
      "Content-Type": "application/json",
      "Cache-Control": "public, max-age=180"
    }
  });
}
__name(jsonResponse, "jsonResponse");
var src_default = {
  async fetch(request, env) {
    try {
      return await handleRequest(request, env);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown worker error";
      return jsonResponse({ error: message }, request, env, 500);
    }
  }
};

// node_modules/wrangler/templates/middleware/middleware-ensure-req-body-drained.ts
var drainBody = /* @__PURE__ */ __name(async (request, env, _ctx, middlewareCtx) => {
  try {
    return await middlewareCtx.next(request, env);
  } finally {
    try {
      if (request.body !== null && !request.bodyUsed) {
        const reader = request.body.getReader();
        while (!(await reader.read()).done) {
        }
      }
    } catch (e) {
      console.error("Failed to drain the unused request body.", e);
    }
  }
}, "drainBody");
var middleware_ensure_req_body_drained_default = drainBody;

// node_modules/wrangler/templates/middleware/middleware-miniflare3-json-error.ts
function reduceError(e) {
  return {
    name: e?.name,
    message: e?.message ?? String(e),
    stack: e?.stack,
    cause: e?.cause === void 0 ? void 0 : reduceError(e.cause)
  };
}
__name(reduceError, "reduceError");
var jsonError = /* @__PURE__ */ __name(async (request, env, _ctx, middlewareCtx) => {
  try {
    return await middlewareCtx.next(request, env);
  } catch (e) {
    const error = reduceError(e);
    return Response.json(error, {
      status: 500,
      headers: { "MF-Experimental-Error-Stack": "true" }
    });
  }
}, "jsonError");
var middleware_miniflare3_json_error_default = jsonError;

// .wrangler/tmp/bundle-oCeNxE/middleware-insertion-facade.js
var __INTERNAL_WRANGLER_MIDDLEWARE__ = [
  middleware_ensure_req_body_drained_default,
  middleware_miniflare3_json_error_default
];
var middleware_insertion_facade_default = src_default;

// node_modules/wrangler/templates/middleware/common.ts
var __facade_middleware__ = [];
function __facade_register__(...args) {
  __facade_middleware__.push(...args.flat());
}
__name(__facade_register__, "__facade_register__");
function __facade_invokeChain__(request, env, ctx, dispatch, middlewareChain) {
  const [head, ...tail] = middlewareChain;
  const middlewareCtx = {
    dispatch,
    next(newRequest, newEnv) {
      return __facade_invokeChain__(newRequest, newEnv, ctx, dispatch, tail);
    }
  };
  return head(request, env, ctx, middlewareCtx);
}
__name(__facade_invokeChain__, "__facade_invokeChain__");
function __facade_invoke__(request, env, ctx, dispatch, finalMiddleware) {
  return __facade_invokeChain__(request, env, ctx, dispatch, [
    ...__facade_middleware__,
    finalMiddleware
  ]);
}
__name(__facade_invoke__, "__facade_invoke__");

// .wrangler/tmp/bundle-oCeNxE/middleware-loader.entry.ts
var __Facade_ScheduledController__ = class {
  constructor(scheduledTime, cron, noRetry) {
    this.scheduledTime = scheduledTime;
    this.cron = cron;
    this.#noRetry = noRetry;
  }
  #noRetry;
  noRetry() {
    if (!(this instanceof __Facade_ScheduledController__)) {
      throw new TypeError("Illegal invocation");
    }
    this.#noRetry();
  }
};
__name(__Facade_ScheduledController__, "__Facade_ScheduledController__");
function wrapExportedHandler(worker) {
  if (__INTERNAL_WRANGLER_MIDDLEWARE__ === void 0 || __INTERNAL_WRANGLER_MIDDLEWARE__.length === 0) {
    return worker;
  }
  for (const middleware of __INTERNAL_WRANGLER_MIDDLEWARE__) {
    __facade_register__(middleware);
  }
  const fetchDispatcher = /* @__PURE__ */ __name(function(request, env, ctx) {
    if (worker.fetch === void 0) {
      throw new Error("Handler does not export a fetch() function.");
    }
    return worker.fetch(request, env, ctx);
  }, "fetchDispatcher");
  return {
    ...worker,
    fetch(request, env, ctx) {
      const dispatcher = /* @__PURE__ */ __name(function(type, init) {
        if (type === "scheduled" && worker.scheduled !== void 0) {
          const controller = new __Facade_ScheduledController__(
            Date.now(),
            init.cron ?? "",
            () => {
            }
          );
          return worker.scheduled(controller, env, ctx);
        }
      }, "dispatcher");
      return __facade_invoke__(request, env, ctx, dispatcher, fetchDispatcher);
    }
  };
}
__name(wrapExportedHandler, "wrapExportedHandler");
function wrapWorkerEntrypoint(klass) {
  if (__INTERNAL_WRANGLER_MIDDLEWARE__ === void 0 || __INTERNAL_WRANGLER_MIDDLEWARE__.length === 0) {
    return klass;
  }
  for (const middleware of __INTERNAL_WRANGLER_MIDDLEWARE__) {
    __facade_register__(middleware);
  }
  return class extends klass {
    #fetchDispatcher = (request, env, ctx) => {
      this.env = env;
      this.ctx = ctx;
      if (super.fetch === void 0) {
        throw new Error("Entrypoint class does not define a fetch() function.");
      }
      return super.fetch(request);
    };
    #dispatcher = (type, init) => {
      if (type === "scheduled" && super.scheduled !== void 0) {
        const controller = new __Facade_ScheduledController__(
          Date.now(),
          init.cron ?? "",
          () => {
          }
        );
        return super.scheduled(controller);
      }
    };
    fetch(request) {
      return __facade_invoke__(
        request,
        this.env,
        this.ctx,
        this.#dispatcher,
        this.#fetchDispatcher
      );
    }
  };
}
__name(wrapWorkerEntrypoint, "wrapWorkerEntrypoint");
var WRAPPED_ENTRY;
if (typeof middleware_insertion_facade_default === "object") {
  WRAPPED_ENTRY = wrapExportedHandler(middleware_insertion_facade_default);
} else if (typeof middleware_insertion_facade_default === "function") {
  WRAPPED_ENTRY = wrapWorkerEntrypoint(middleware_insertion_facade_default);
}
var middleware_loader_entry_default = WRAPPED_ENTRY;
export {
  __INTERNAL_WRANGLER_MIDDLEWARE__,
  middleware_loader_entry_default as default
};
//# sourceMappingURL=index.js.map
