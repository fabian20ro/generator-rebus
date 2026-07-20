import { afterEach, describe, expect, test, vi } from "vitest";

import { routeRequest } from "./router";
import type { Env } from "../shared/cors";

const env: Env = {
  SUPABASE_URL: "https://example.supabase.co",
  SUPABASE_ANON_KEY: "anon-test-key",
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

  test("uses only the anon key and published filter for puzzle details", async () => {
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
    expect(options.headers.apikey).toBe("anon-test-key");
    expect(options.headers.Authorization).toBe("Bearer anon-test-key");
  });
});
