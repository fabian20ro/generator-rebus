#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: ./run_definition_improve.sh [options]

Wrapper over:
  python -m generator.redefine --all

Options:
  --debug    Verbose streamed LM Studio reasoning/output logs in run.log

Examples:
  ./run_definition_improve.sh
  ./run_definition_improve.sh --debug
  ./run_definition_improve.sh --debug --rounds 3
EOF
  exit 0
fi

exec .venv/bin/python -m generator.redefine --all "$@"
