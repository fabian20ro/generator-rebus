#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: ./run_clue_canon_backfill.sh [--apply|--dry-run] [extra args]

Wrapper over:
  python -m generator.clue_canon backfill

Examples:
  ./run_clue_canon_backfill.sh --dry-run
  ./run_clue_canon_backfill.sh --apply
  ./run_clue_canon_backfill.sh --apply --resume
  ./run_clue_canon_backfill.sh --dry-run --word APA --limit 10 --min-count 3
  ./run_clue_canon_backfill.sh --apply --progress-every 10
  ./run_clue_canon_backfill.sh --apply --word-queue-size 50 --referee-batch-size 50
EOF
  exit 0
fi

PYTHON_BIN="python3"
if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
fi

has_mode=0
for arg in "$@"; do
  if [[ "$arg" == "--apply" || "$arg" == "--dry-run" ]]; then
    has_mode=1
    break
  fi
done

if [[ "$#" -eq 0 ]]; then
  args=(--apply)
else
  args=("$@")
  if [[ "$has_mode" -eq 0 ]]; then
    args=(--apply "${args[@]}")
  fi
fi

exec "$PYTHON_BIN" -m generator.clue_canon backfill "${args[@]}"
