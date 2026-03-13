#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_LOG="$ROOT_DIR/generator/output/loop_runner.log"

mkdir -p "$ROOT_DIR/generator/output"

cd "$ROOT_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] batch loop started" >> "$RUN_LOG"

while true; do
  seed="$(( $(date +%s) + RANDOM ))"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] starting batch seed=$seed sizes=7,10,12" >> "$RUN_LOG"

  if PYTHONUNBUFFERED=1 .venv/bin/python -m generator.batch_publish \
    --sizes 7 10 12 \
    --rewrite-rounds 4 \
    --preparation-attempts 5 \
    --seed "$seed" >> "$RUN_LOG" 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] batch completed seed=$seed" >> "$RUN_LOG"
  else
    status=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] batch failed seed=$seed exit=$status" >> "$RUN_LOG"
  fi

  sleep 2
done
