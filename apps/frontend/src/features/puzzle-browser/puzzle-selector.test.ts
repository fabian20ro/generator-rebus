/** @jest-environment jsdom */

import { renderPuzzleList } from "./puzzle-selector";
import type { PuzzleTabItem } from "../gamification/puzzle-status";

describe("puzzle selector", () => {
  test("renders remote puzzle text without interpreting markup", () => {
    const container = document.createElement("div");
    const puzzle = {
      id: "p1",
      title: '<img src=x onerror="window.__xss=1">',
      description: "<script>window.__xss=1</script>",
      grid_size: 7,
      difficulty: 3,
      created_at: "2026-07-21T00:00:00Z",
    } as PuzzleTabItem;

    renderPuzzleList(container, [puzzle], () => undefined, "available");

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("h3")?.textContent).toBe(puzzle.title);
    expect(container.querySelector(".puzzle-card__theme")?.textContent).toBe(puzzle.description);
  });
});
