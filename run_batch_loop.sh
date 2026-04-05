#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: ./run_batch_loop.sh [options]

Wrapper over:
  python -m generator.loop_controller --auto-size

Options:
  --debug    Verbose streamed LM Studio reasoning/output logs in each batch run.log

Examples:
  ./run_batch_loop.sh
  ./run_batch_loop.sh --debug
  ./run_batch_loop.sh --debug --sleep-seconds 10
EOF
  exit 0
fi

cargo build --release --manifest-path "$ROOT_DIR/crossword_engine/Cargo.toml"

exec .venv/bin/python -m generator.loop_controller --auto-size "$@"
