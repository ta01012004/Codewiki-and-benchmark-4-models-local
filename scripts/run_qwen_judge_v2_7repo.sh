#!/usr/bin/env bash
set -euo pipefail

cd /home/22011107/TA/NLPCODEWIKI/CodeWiki
source ~/miniconda3/etc/profile.d/conda.sh
conda activate codewiki_py312
export CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0}
export HF_DATASETS_OFFLINE=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

python codewiki/eval_codewikibench_local \
  --judge_model /work/$USER/models/Qwen2.5-Coder-7B-Instruct \
  --docs_root outputs/docs \
  --output_csv outputs/results_v2_improved_7repo/judge_qwen_repo_v2_7repo.csv \
  --split train \
  --model_keys "CodeLlama,DeepSeekCoder,Mistral,Qwen" \
  --repos "OpenHands,svelte,puppeteer,ml-agents,logstash,wazuh,electron" \
  --max_doc_chars 8000 \
  --max_leaves 80 \
  --progress_every 20 \
  --offline

python scripts/postprocess_qwen_judge_v2_7repo.py
