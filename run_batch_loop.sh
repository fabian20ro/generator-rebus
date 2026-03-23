#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

cargo build --release --manifest-path "$ROOT_DIR/crossword_engine/Cargo.toml"

exec .venv/bin/python -m generator.loop_controller "$@"
