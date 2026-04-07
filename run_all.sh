#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: ./run_all.sh [options]

Wrapper over:
  python -m generator.run_all

Options:
  --debug    Verbose streamed LM Studio reasoning/output logs in run.log

Examples:
  ./run_all.sh
  ./run_all.sh --debug
  ./run_all.sh --topics retitle,redefine
EOF
  exit 0
fi

cargo build --release --manifest-path "$ROOT_DIR/crossword_engine/Cargo.toml"

exec .venv/bin/python -m generator.run_all "$@"
