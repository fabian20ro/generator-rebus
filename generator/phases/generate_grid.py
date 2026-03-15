"""Phase 2: Generate a grid template with black squares."""

from __future__ import annotations
from ..core.grid_template import generate_procedural_template, validate_template
from ..core.markdown_io import write_grid_template


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Generate a random grid template and save as markdown."""
    size = kwargs.get("size", 10)

    print(f"Generating {size}x{size} grid template...")
    grid = generate_procedural_template(size)
    if grid is None:
        print(f"Error: could not generate a valid template for size {size}")
        return

    valid, msg = validate_template(grid)
    if not valid:
        print(f"Error: generated template is invalid: {msg}")
        return

    # Count stats
    letter_cells = sum(1 for row in grid for cell in row if cell)
    black_cells = size * size - letter_cells
    print(f"  Letter cells: {letter_cells}")
    print(f"  Black squares: {black_cells}")

    md = write_grid_template(size, grid)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Saved template to {output_file}")
