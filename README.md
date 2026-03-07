# R2poWiki Evaluation Platform

<p align="center">
  <strong>Graph-aware repository documentation and CodeWikiBench evaluation with local open-source LLMs</strong>
</p>

<p align="center">
  <img src="./img/framework-overview.png" alt="Pipeline overview" width="760" />
</p>

This repository is the current default implementation of a **CodeWiki-inspired repository documentation pipeline** built for **fully local execution**. It generates architecture-level Markdown documentation, evaluates outputs on **CodeWikiBench**, and stores benchmark artifacts in a single default layout.

## What Is Default Now

- Default pipeline: **V2**
- Default documentation output: `outputs/docs/<model>/<repo>.md`
- Default benchmark output: `outputs/results/*.csv`
- Legacy V1 is still runnable with `--pipeline_version v1`, but it writes to `outputs/docs_v1`, `outputs/results_v1`, and `outputs/cache_v1`

## Core V2 Ideas

- **Graph / hierarchical decomposition**
  - Build an architecture IR JSON from file nodes, directory nodes, import edges, config/build anchors, and lightweight community detection
  - Render Mermaid from the IR instead of letting the model invent diagrams
- **RAG + hierarchical summarization**
  - Chunk code/docs/config files structurally
  - Summarize chunks, then summarize subsystems, then synthesize final sections with section-specific retrieval
- **Local-only inference**
  - Supports `transformers` and `vllm`
  - Designed for gateway prefetch + offline GPU-node execution

## Models

- `meta-llama/CodeLlama-7b-Instruct-hf`
- `deepseek-ai/deepseek-coder-6.7b-instruct`
- `mistralai/Mistral-7B-Instruct-v0.3`
- `Qwen/Qwen2.5-Coder-7B-Instruct`

## Repository Layout

```text
codewiki_hpc/
  run.py
  config.py
  dataset.py
  repo_manager.py
  file_selector.py
  summarizer.py
  summarizer_v2.py
  decomposition_v2.py
  prompts.py
  prompts_v2.py
  inference/
  evaluation/
  utils/
configs/
  default.yaml
scripts/
  prefetch_gateway.sh
  slurm_array_generate.sh
  slurm_array_eval.sh
outputs/
  docs/
  results/
```

## Output Artifacts

### Generated documentation

- `outputs/docs/CodeLlama/*.md`
- `outputs/docs/DeepSeekCoder/*.md`
- `outputs/docs/Mistral/*.md`
- `outputs/docs/Qwen/*.md`

### Benchmark results

- `outputs/results/cwbench_CodeLlama-7b-Instruct-hf.csv`
- `outputs/results/cwbench_deepseek-coder-6.7b-instruct.csv`
- `outputs/results/cwbench_Mistral-7B-Instruct-v0.3.csv`
- `outputs/results/cwbench_Qwen2.5-Coder-7B-Instruct.csv`
- `outputs/results/cwbench_all_models.csv`
- `outputs/results/cwbench_matrix_repo.csv`
- `outputs/results/cwbench_matrix_model.csv`

## Setup

```bash
cd /home/22011107/TA/NLPCODEWIKI/CodeWiki
conda activate codewiki_py312
pip install -r requirements.txt
```

## Gateway Prefetch

Run on the gateway node to populate the git cache before moving to an offline GPU node:

```bash
cd /home/22011107/TA/NLPCODEWIKI/CodeWiki
./scripts/prefetch_gateway.sh
```

This fills `outputs/cache/repos/`.

## Run The Default Pipeline

### Full local benchmark

```bash
python -m codewiki_hpc.run \
  --config configs/default.yaml \
  --models "CodeLlama,DeepSeekCoder,Mistral,Qwen" \
  --split train \
  --max_repos 22 \
  --backend transformers \
  --offline \
  --model_root /work/$USER/models \
  --output_dir outputs \
  --resume
```

### Run the 7 paper repos only

```bash
python -m codewiki_hpc.run \
  --config configs/default.yaml \
  --models "CodeLlama,DeepSeekCoder,Mistral,Qwen" \
  --repos "OpenHands,svelte,puppeteer,ml-agents,logstash,wazuh,electron" \
  --split train \
  --max_repos 22 \
  --backend transformers \
  --offline \
  --model_root /work/$USER/models \
  --output_dir outputs \
  --resume
```

### Evaluation only on existing docs

```bash
python -m codewiki_hpc.run \
  --config configs/default.yaml \
  --models "CodeLlama,DeepSeekCoder,Mistral,Qwen" \
  --repos "OpenHands,svelte,puppeteer,ml-agents,logstash,wazuh,electron" \
  --split train \
  --max_repos 22 \
  --output_dir outputs \
  --eval_only
```

### Run legacy V1 explicitly

```bash
python -m codewiki_hpc.run \
  --config configs/default.yaml \
  --models "Qwen" \
  --split train \
  --max_repos 1 \
  --backend transformers \
  --offline \
  --model_root /work/$USER/models \
  --output_dir outputs \
  --pipeline_version v1
```

## Token Settings

Current defaults from `configs/default.yaml`:

| Model | max_input_tokens | max_new_tokens | temperature | top_p | timeout_sec |
|---|---:|---:|---:|---:|---:|
| CodeLlama | backend default | 768 | 0.1 | 0.9 | 180 |
| DeepSeekCoder | 3072 | 768 | 0.1 | 0.9 | 180 |
| Mistral | backend default | 768 | 0.1 | 0.9 | 180 |
| Qwen | backend default | 768 | 0.1 | 0.9 | 180 |

## Evaluation Notes

- The default in `codewiki_hpc.run` is the existing local heuristic evaluator
- Additional judge-style evaluation utilities remain in the repo for paper-style experiments
- For paper-consistent comparisons, prefer running on the same 7 repositories used in your analysis subset

## Practical Notes

- GPU nodes can be offline; use gateway prefetch first
- If interrupted, rerun with `--resume`
- If a repo clone is missing in offline mode, repopulate `outputs/cache/repos` from the gateway
- The default path layout now reflects the V2 pipeline only

## License

MIT, with upstream dependencies and benchmark data subject to their own licenses
