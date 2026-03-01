#!/usr/bin/env bash
#SBATCH --job-name=codewiki-gen
#SBATCH --output=outputs/logs/slurm_gen_%A_%a.out
#SBATCH --error=outputs/logs/slurm_gen_%A_%a.err
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=24:00:00
#SBATCH --array=0-21

set -euo pipefail

export HF_HOME=${HF_HOME:-$PWD/.hf_home}
export TRANSFORMERS_CACHE=${TRANSFORMERS_CACHE:-$HF_HOME/transformers}
export TOKENIZERS_PARALLELISM=${TOKENIZERS_PARALLELISM:-false}

MODELS=${MODELS:-"CodeLlama,DeepSeekCoder,Mistral,Qwen"}
SPLIT=${SPLIT:-train}
MAX_REPOS=${MAX_REPOS:-22}
BACKEND=${BACKEND:-vllm}
OUTPUT_DIR=${OUTPUT_DIR:-outputs}
OFFLINE=${OFFLINE:-1}
MODEL_ROOT=${MODEL_ROOT:-}

mkdir -p "$OUTPUT_DIR/logs"

EXTRA_ARGS=()
if [[ "$OFFLINE" == "1" ]]; then
  EXTRA_ARGS+=(--offline)
fi
if [[ -n "$MODEL_ROOT" ]]; then
  EXTRA_ARGS+=(--model_root "$MODEL_ROOT")
fi

python -m codewiki_hpc.run \
  --config configs/default.yaml \
  --models "$MODELS" \
  --split "$SPLIT" \
  --max_repos "$MAX_REPOS" \
  --backend "$BACKEND" \
  --output_dir "$OUTPUT_DIR" \
  --resume \
  --array_index "$SLURM_ARRAY_TASK_ID" \
  --array_total "$SLURM_ARRAY_TASK_COUNT" \
  "${EXTRA_ARGS[@]}"
