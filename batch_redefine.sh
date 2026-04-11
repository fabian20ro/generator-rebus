#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

exec uv run python -m rebus_generator.workflows.redefine.repair "$@"
