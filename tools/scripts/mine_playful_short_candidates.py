#!/usr/bin/env python3
"""Write review-only playful two-letter reduction candidates."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rebus_generator.evaluation.playful_reduction_miner import (
    load_words,
    mine_playful_short_candidates,
    write_candidates,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Mine playful short-answer candidates")
    parser.add_argument("--words", default="build/words.json")
    parser.add_argument("--output", default="build/curation/playful_short_candidates.json")
    parser.add_argument("--max-candidates-per-word", type=int, default=2)
    args = parser.parse_args()

    candidates = mine_playful_short_candidates(
        load_words(Path(args.words)),
        max_candidates_per_word=max(1, args.max_candidates_per_word),
    )
    output = write_candidates(candidates, Path(args.output))
    print(output)


if __name__ == "__main__":
    main()
