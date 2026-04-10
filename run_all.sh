#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

WORDS_PATH="build/words.json"
ARGS=("$@")
index=1
while [[ $index -le $# ]]; do
  value="${ARGS[$index]}"
  if [[ "$value" == "--words" && $((index + 1)) -le $# ]]; then
    WORDS_PATH="${ARGS[$((index + 1))]}"
    index=$((index + 2))
    continue
  fi
  if [[ "$value" == --words=* ]]; then
    WORDS_PATH="${value#--words=}"
  fi
  index=$((index + 1))
done

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: ./run_all.sh [options]

Wrapper over:
  python -m rebus_generator.cli.run_all

Options:
  --debug    Verbose streamed LM Studio reasoning/output logs in run.log

Examples:
  ./run_all.sh
  ./run_all.sh --debug
  ./run_all.sh --topics retitle,redefine
EOF
  exit 0
fi

cargo build --release --manifest-path "$ROOT_DIR/engines/crossword-engine/Cargo.toml"
.venv/bin/python -m rebus_generator profile "$WORDS_PATH" -

exec .venv/bin/python -m rebus_generator.cli.run_all "$@"
