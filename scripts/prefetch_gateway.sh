#!/usr/bin/env bash
set -euo pipefail

# Run this on gateway/login node (where internet access is available).
cd "$(dirname "$0")/.."

SPLIT=${SPLIT:-train}
MAX_REPOS=${MAX_REPOS:-22}
OUTPUT_DIR=${OUTPUT_DIR:-outputs}
START=${START:-0}
END=${END:-}

ARGS=(
  --config configs/default.yaml
  --split "$SPLIT"
  --max_repos "$MAX_REPOS"
  --output_dir "$OUTPUT_DIR"
  --start "$START"
)
if [[ -n "$END" ]]; then
  ARGS+=(--end "$END")
fi

python -m codewiki_hpc.prefetch "${ARGS[@]}"
