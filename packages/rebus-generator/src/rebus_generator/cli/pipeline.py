#!/usr/bin/env python3
"""Romanian Rebus Generator - CLI entry point.

Usage:
    python -m rebus_generator <phase> <input_file> <output_file> [options]

Phases:
    download       Download words from Supabase
    theme          Find a theme using LM Studio
    define         Generate definitions using LM Studio
    verify         Verify definitions (AI guesses the word)
    upload         Upload puzzle to Supabase
    activate       Activate a puzzle (make it visible)
    deactivate     Deactivate a puzzle

Examples:
    python -m rebus_generator download - build/words.json
    python -m rebus_generator theme build/filled.md build/themed.md
    python -m rebus_generator define build/themed.md build/defs.md
    python -m rebus_generator verify build/defs.md build/verified.md
    python -m rebus_generator upload build/verified.md -
    python -m rebus_generator activate <puzzle-id>
    python -m rebus_generator deactivate <puzzle-id>
"""

import argparse
import sys

from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.io.runtime_logging import (
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
            from rebus_generator.workflows.generate.download import run
        elif phase == "theme":
            from rebus_generator.workflows.retitle.titleing import run
        elif phase == "define":
            from rebus_generator.workflows.generate.define import run
        elif phase == "verify":
            from rebus_generator.workflows.generate.verify import run
        elif phase == "upload":
            from rebus_generator.workflows.generate.upload import run
        elif phase == "activate":
            from rebus_generator.workflows.generate.activate import run
        elif phase == "deactivate":
            from rebus_generator.workflows.generate.activate import run
            kwargs["deactivate"] = True
        else:
            parser.print_help()
            sys.exit(1)

        run(args.input_file, args.output_file, **kwargs)
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
