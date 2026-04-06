import re

with open('frontend/src/components/grid-renderer.ts', 'r') as f:
    content = f.read()

# Add cache map
content = re.sub(
    r'(const cellRefs = new Map<[\s\S]*?>\(\);)',
    r'\1\n\n/** Cache to prevent unnecessary DOM updates. Maps "r,c" to a serialized state string. */\nconst cellRenderCache = new Map<string, string>();',
    content
)

# Clear cache on createGrid
content = re.sub(
    r'(cellRefs\.clear\(\);\n\s*createdSize = state\.size;)',
    r'\1\n  cellRenderCache.clear();',
    content
)

# Replace updateGrid inner loop logic
old_inner_loop = """      const isPencil =
        !!cellValue && cellValue !== "!" && state.pencilCells[r][c];

      cell.classList.toggle("cell--active", isActive);
      cell.classList.toggle("cell--highlight", isHighlight);
      cell.classList.toggle("cell--wrong", isWrong);
      cell.classList.toggle("cell--revealed", isRevealed);
      cell.classList.toggle("cell--pencil", isPencil);

      if (isActive) {
        input.setAttribute("aria-current", "true");
      } else {
        input.removeAttribute("aria-current");
      }

      input.inputMode = state.touchRemoteEnabled ? "none" : "text";
      input.readOnly = state.isSolvedView || state.touchRemoteEnabled;

      // Update input value only when it differs (avoids cursor jump)
      const displayVal = cellValue && cellValue !== "!" && cellValue !== "#" ? cellValue : "";
      if (input.value !== displayVal) {
        input.value = displayVal;
      }"""

new_inner_loop = """      const isPencil =
        !!cellValue && cellValue !== "!" && state.pencilCells[r][c];

      const displayVal = cellValue && cellValue !== "!" && cellValue !== "#" ? cellValue : "";
      const inputMode = state.touchRemoteEnabled ? "none" : "text";
      const readOnly = state.isSolvedView || state.touchRemoteEnabled;

      // Serialize the presentation state
      const stateString = `${isActive}|${isHighlight}|${isWrong}|${isRevealed}|${isPencil}|${displayVal}|${inputMode}|${readOnly}`;

      // Skip DOM updates if nothing visual changed
      if (cellRenderCache.get(`${r},${c}`) === stateString) continue;
      cellRenderCache.set(`${r},${c}`, stateString);

      cell.classList.toggle("cell--active", isActive);
      cell.classList.toggle("cell--highlight", isHighlight);
      cell.classList.toggle("cell--wrong", isWrong);
      cell.classList.toggle("cell--revealed", isRevealed);
      cell.classList.toggle("cell--pencil", isPencil);

      if (isActive) {
        input.setAttribute("aria-current", "true");
      } else {
        input.removeAttribute("aria-current");
      }

      input.inputMode = inputMode;
      input.readOnly = readOnly;

      // Update input value only when it differs (avoids cursor jump)
      if (input.value !== displayVal) {
        input.value = displayVal;
      }"""

content = content.replace(old_inner_loop, new_inner_loop)

with open('frontend/src/components/grid-renderer.ts', 'w') as f:
    f.write(content)
