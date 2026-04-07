#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: ./run_title_improve.sh [options]

Wrapper over:
  ./run_all.sh --topics retitle

Options:
  --debug    Verbose streamed LM Studio reasoning/output logs in run.log

Examples:
  ./run_title_improve.sh
  ./run_title_improve.sh --debug
  ./run_title_improve.sh --debug
EOF
  exit 0
fi

exec "$ROOT_DIR/run_all.sh" --topics retitle "$@"
