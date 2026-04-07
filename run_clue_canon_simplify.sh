#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: ./run_clue_canon_simplify.sh [--apply|--dry-run] [extra args]

Wrapper over:
  python -m generator.run_all --topics simplify

Options:
  --debug    Verbose streamed LM Studio reasoning/output logs in run.log

Examples:
  ./run_clue_canon_simplify.sh --apply --simplify-batch-size 40
  ./run_clue_canon_simplify.sh --apply --debug --simplify-batch-size 40
  ./run_clue_canon_simplify.sh --dry-run --simplify-batch-size 20
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

translated=(--topics simplify)
for arg in "${args[@]}"; do
  if [[ "$arg" == "--apply" ]]; then
    continue
  fi
  translated+=("$arg")
done

exec "$PYTHON_BIN" -m generator.run_all "${translated[@]}"
