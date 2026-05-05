from __future__ import annotations


def _is_letter(ch: str) -> bool:
    return ch not in {"#", "?"}


def extract_words(grid: list[list[str]]) -> tuple[list[str], list[str]]:
    n = len(grid)
    across: list[str] = []
    down: list[str] = []

    for r in range(n):
        c = 0
        while c < n:
            if _is_letter(grid[r][c]) and (c == 0 or not _is_letter(grid[r][c - 1])):
                start = c
                while c < n and _is_letter(grid[r][c]):
                    c += 1
                word = "".join(grid[r][start:c])
                if len(word) >= 2:
                    across.append(word)
            else:
                c += 1

    for c in range(n):
        r = 0
        while r < n:
            if _is_letter(grid[r][c]) and (r == 0 or not _is_letter(grid[r - 1][c])):
                start = r
                while r < n and _is_letter(grid[r][c]):
                    r += 1
                word = "".join(grid[i][c] for i in range(start, r))
                if len(word) >= 2:
                    down.append(word)
            else:
                r += 1

    return across, down
