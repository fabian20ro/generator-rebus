import { afterEach, describe, expect, test, vi } from "vitest";

import { routeRequest } from "./router";
import type { Env } from "../shared/cors";

const env: Env = {
  SUPABASE_URL: "https://example.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "publishable-test-key",
  ALLOWED_ORIGINS: "https://app.example",
};

afterEach(() => vi.unstubAllGlobals());

describe("worker router", () => {
  test("rejects an untrusted browser origin before proxying", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await routeRequest(
      new Request("https://worker.example/puzzles", {
        headers: { Origin: "https://evil.example" },
      }),
      env,
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("uses only the publishable key and published filter for puzzle details", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify([{
        id: "00000000-0000-0000-0000-000000000001",
        title: "Test",
      }])))
      .mockResolvedValueOnce(new Response(JSON.stringify([])));
    vi.stubGlobal("fetch", fetchMock);

    const response = await routeRequest(
      new Request("https://worker.example/puzzles/00000000-0000-0000-0000-000000000001"),
      env,
    );

    expect(response.status).toBe(200);
    const [url, options] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("published=eq.true");
    expect(options.headers.apikey).toBe("publishable-test-key");
    expect(options.headers.Authorization).toBeUndefined();
  });

  test("keeps legacy catalog visibility when the quality rollout is disabled", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify([])));
    vi.stubGlobal("fetch", fetchMock);

    await routeRequest(new Request("https://worker.example/puzzles"), env);

    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("published=eq.true");
    expect(String(url)).not.toContain("pass_rate=gte");
    expect(String(url)).not.toContain("rebus_score_min=gte");
  });

  test("quality rollout filters list, detail, and solution consistently", async () => {
    const rolloutEnv: Env = {
      ...env,
      CATALOG_QUALITY_FILTER: "true",
      CATALOG_MIN_PASS_RATE: "0.5",
      CATALOG_MIN_REBUS_SCORE: "5",
    };
    const puzzleId = "00000000-0000-0000-0000-000000000001";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify([])))
      .mockResolvedValueOnce(new Response(JSON.stringify([{ id: puzzleId, title: "Test" }])))
      .mockResolvedValueOnce(new Response(JSON.stringify([])))
      .mockResolvedValueOnce(new Response(JSON.stringify([{ grid_solution: "[]" }])));
    vi.stubGlobal("fetch", fetchMock);

    await routeRequest(new Request("https://worker.example/puzzles"), rolloutEnv);
    await routeRequest(new Request(`https://worker.example/puzzles/${puzzleId}`), rolloutEnv);
    await routeRequest(new Request(`https://worker.example/puzzles/${puzzleId}/solution`), rolloutEnv);

    for (const callIndex of [0, 1, 3]) {
      const url = String(fetchMock.mock.calls[callIndex][0]);
      expect(url).toContain("published=eq.true");
      expect(url).toContain("pass_rate=gte.0.5");
      expect(url).toContain("rebus_score_min=gte.5");
    }
    expect(String(fetchMock.mock.calls[0][0])).toContain("rebus_score_min");
  });
});
