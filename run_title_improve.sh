#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: ./run_title_improve.sh [options]

Wrapper over:
  python -m generator.retitle --all

Options:
  --debug    Verbose streamed LM Studio reasoning/output logs in run.log

Examples:
  ./run_title_improve.sh
  ./run_title_improve.sh --debug
  ./run_title_improve.sh --debug --duplicates-only
EOF
  exit 0
fi

exec .venv/bin/python -m generator.retitle --all "$@"
