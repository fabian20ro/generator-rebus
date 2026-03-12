"""Phase 8: Activate or deactivate a puzzle."""

from __future__ import annotations
import sys
from supabase import create_client
from ..config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY


def set_published(puzzle_id: str, published: bool) -> str:
    """Set published state and return the puzzle title."""
    if not puzzle_id or puzzle_id == "-":
        print("Error: puzzle ID is required. Usage: python rebus.py activate <puzzle-id>")
        sys.exit(1)

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        sys.exit(1)

    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    new_state = published
    action = "Activating" if new_state else "Deactivating"
    print(f"{action} puzzle {puzzle_id}...")

    result = (client.table("crossword_puzzles")
              .update({"published": new_state})
              .eq("id", puzzle_id)
              .execute())

    if result.data:
        title = result.data[0].get("title", "Untitled")
        print(f"{'Activated' if new_state else 'Deactivated'}: {title} ({puzzle_id})")
        return title
    else:
        print(f"Error: puzzle {puzzle_id} not found")
        sys.exit(1)


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Activate or deactivate a puzzle by ID.

    input_file is used as the puzzle ID for this phase.
    """
    deactivate = kwargs.get("deactivate", False)
    set_published(input_file, not deactivate)
