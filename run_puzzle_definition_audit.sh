#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: ./run_puzzle_definition_audit.sh [options]

Wrapper over:
  python -m rebus_generator.workflows.canonicals.puzzle_definition_audit

Examples:
  ./run_puzzle_definition_audit.sh
  ./run_puzzle_definition_audit.sh --published-only
  ./run_puzzle_definition_audit.sh --puzzle-id <uuid>
  ./run_puzzle_definition_audit.sh --limit 25
  ./run_puzzle_definition_audit.sh --output build/puzzle_definition_audit/latest.json
  ./run_puzzle_definition_audit.sh --details build/puzzle_definition_audit/latest.jsonl
EOF
  exit 0
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v uv &> /dev/null; then
    PYTHON_BIN="uv run python"
  elif [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

exec "$PYTHON_BIN" -m rebus_generator.workflows.canonicals.puzzle_definition_audit "$@"
