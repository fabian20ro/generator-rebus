#!/usr/bin/env python3
"""Romanian Rebus Generator - CLI entry point.

Usage:
    python -m generator.rebus <phase> <input_file> <output_file> [options]

Phases:
    download       Download words from Supabase
    generate-grid  Generate a grid template with black squares
    fill           Fill grid with words (CSP backtracking)
    theme          Find a theme using LM Studio
    define         Generate definitions using LM Studio
    verify         Verify definitions (AI guesses the word)
    upload         Upload puzzle to Supabase
    activate       Activate a puzzle (make it visible)
    deactivate     Deactivate a puzzle

Examples:
    python -m generator.rebus download - output/words.json
    python -m generator.rebus generate-grid - output/grid.md --size 10
    python -m generator.rebus fill output/grid.md output/filled.md --words output/words.json
    python -m generator.rebus theme output/filled.md output/themed.md
    python -m generator.rebus define output/themed.md output/defs.md
    python -m generator.rebus verify output/defs.md output/verified.md
    python -m generator.rebus upload output/verified.md -
    python -m generator.rebus activate <puzzle-id>
    python -m generator.rebus deactivate <puzzle-id>
"""

import argparse
import sys

from .config import VERIFY_CANDIDATE_COUNT
from .core.size_tuning import SUPPORTED_GRID_SIZES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Romanian Rebus Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("phase", choices=[
        "download", "generate-grid", "fill", "theme",
        "define", "verify", "upload", "activate", "deactivate",
    ])
    parser.add_argument("input_file", help="Input file path (use '-' for none)")
    parser.add_argument("output_file", nargs="?", default="-",
                        help="Output file path (use '-' for stdout/none)")
    parser.add_argument("--size", type=int, default=10, choices=list(SUPPORTED_GRID_SIZES),
                        help="Grid size (default: 10)")
    parser.add_argument("--words", type=str,
                        help="Path to words.json (for fill phase)")
    parser.add_argument("--max-rarity", type=int, default=5,
                        help="Max word rarity level 1-5 (default: 5)")
    parser.add_argument("--max-backtracks", type=int, default=50000,
                        help="Max backtracks for solver (default: 50000)")
    parser.add_argument("--force", action="store_true",
                        help="Force upload even with unverified definitions")
    parser.add_argument(
        "--verify-candidates",
        type=int,
        default=VERIFY_CANDIDATE_COUNT,
        help=f"How many verifier candidates to request (default: {VERIFY_CANDIDATE_COUNT})",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    kwargs = {
        "size": args.size,
        "words": args.words,
        "max_rarity": args.max_rarity,
        "max_backtracks": args.max_backtracks,
        "force": args.force,
        "verify_candidates": args.verify_candidates,
    }

    phase = args.phase

    if phase == "download":
        from .phases.download import run
    elif phase == "generate-grid":
        from .phases.generate_grid import run
    elif phase == "fill":
        from .phases.fill import run
    elif phase == "theme":
        from .phases.theme import run
    elif phase == "define":
        from .phases.define import run
    elif phase == "verify":
        from .phases.verify import run
    elif phase == "upload":
        from .phases.upload import run
    elif phase == "activate":
        from .phases.activate import run
    elif phase == "deactivate":
        from .phases.activate import run
        kwargs["deactivate"] = True
    else:
        parser.print_help()
        sys.exit(1)

    run(args.input_file, args.output_file, **kwargs)


if __name__ == "__main__":
    main()
