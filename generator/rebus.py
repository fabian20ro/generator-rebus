#!/usr/bin/env python3
"""Romanian Rebus Generator - CLI entry point.

Usage:
    python -m generator.rebus <phase> <input_file> <output_file> [options]

Phases:
    download       Download words from Supabase
    theme          Find a theme using LM Studio
    define         Generate definitions using LM Studio
    verify         Verify definitions (AI guesses the word)
    upload         Upload puzzle to Supabase
    activate       Activate a puzzle (make it visible)
    deactivate     Deactivate a puzzle

Examples:
    python -m generator.rebus download - output/words.json
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
from .core.runtime_logging import (
    add_llm_debug_argument,
    install_process_logging,
    path_timestamp,
    set_llm_debug_enabled,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Romanian Rebus Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("phase", choices=[
        "download", "theme",
        "define", "verify", "upload", "activate", "deactivate",
    ])
    parser.add_argument("input_file", help="Input file path (use '-' for none)")
    parser.add_argument("output_file", nargs="?", default="-",
                        help="Output file path (use '-' for stdout/none)")
    parser.add_argument("--force", action="store_true",
                        help="Force upload even with unverified definitions")
    parser.add_argument(
        "--verify-candidates",
        type=int,
        default=VERIFY_CANDIDATE_COUNT,
        help=f"How many verifier candidates to request (default: {VERIFY_CANDIDATE_COUNT})",
    )
    add_llm_debug_argument(parser)

    return parser


def main():
    handle = install_process_logging(
        run_id=f"rebus_{path_timestamp()}",
        component="rebus",
        tee_console=True,
    )
    parser = build_parser()
    try:
        args = parser.parse_args()
        set_llm_debug_enabled(args.debug)

        kwargs = {
            "force": args.force,
            "verify_candidates": args.verify_candidates,
        }

        phase = args.phase

        if phase == "download":
            from .phases.download import run
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
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
