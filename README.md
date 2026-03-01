# CodeWiki + CodeWikiBench (Local 4-Model HPC Benchmark)

<p align="center">
  <strong>Holistic repository documentation with fully local open-source LLMs</strong>
</p>

<p align="center">
  <img src="./img/framework-overview.png" alt="Pipeline overview" width="760" />
</p>

This repository is my personal implementation and benchmark runner for **CodeWiki-style repository documentation generation** on **CodeWikiBench**, using 4 local models on HPC.

## Highlights

- Fully local inference: no paid API
- Two-backend design: `transformers` + `vllm`
- HPC-compatible workflow (gateway prefetch + GPU offline run)
- Resumable execution, caching, retry for git operations, OOM fallback
- Per-model benchmark CSV + merged summary CSV

## Models Evaluated

- `meta-llama/CodeLlama-7b-Instruct-hf`
- `deepseek-ai/deepseek-coder-6.7b-instruct`
- `mistralai/Mistral-7B-Instruct-v0.3`
- `Qwen/Qwen2.5-Coder-7B-Instruct`

## Token Settings Per Model

The table below reflects the current generation settings in `configs/default.yaml`.

| Model Alias | max_input_tokens | max_new_tokens | temperature | top_p | timeout_sec |
|---|---:|---:|---:|---:|---:|
| CodeLlama | default backend limit | 768 | 0.1 | 0.9 | 180 |
| DeepSeekCoder | 3072 | 768 | 0.1 | 0.9 | 180 |
| Mistral | default backend limit | 768 | 0.1 | 0.9 | 180 |
| Qwen | default backend limit | 768 | 0.1 | 0.9 | 180 |

Notes:
- `max_input_tokens` is explicitly set for `DeepSeekCoder`; other models use backend defaults.
- For `transformers`, the default input limit in this project is `3072` tokens unless overridden per model.

## Current Results (train split, 22 repos)

> QA is intentionally omitted in this table as requested.

| Model | Completed Repos | Mean ROUGE-L | Mean Coverage |
|---|---:|---:|---:|
| CodeLlama-7b-Instruct-hf | 22/22 | 0.0212 | 0.6431 |
| deepseek-coder-6.7b-instruct | 21/22 | 0.0173 | 0.5851 |
| Mistral-7B-Instruct-v0.3 | 22/22 | 0.0231 | 0.6201 |
| Qwen2.5-Coder-7B-Instruct | 22/22 | 0.0283 | 0.6309 |

## Compare With CodeWiki Paper (Context)

This repo is **not** a direct reproduction of the exact paper setup; it is a practical local-HPC variant.

- CodeWiki paper: broader framework, often stronger frontier-model setup and richer generation stack.
- This repo: emphasizes reproducibility with **local 7B-class OSS models**, offline GPU nodes, and robust engineering workflow.
- Therefore, absolute scores are expected to differ; the main value here is **stable local benchmarking + deployable HPC pipeline**.

## Project Structure

```text
codewiki_hpc/
  run.py
  prefetch.py
  config.py
  dataset.py
  repo_manager.py
  file_selector.py
  summarizer.py
  prompts.py
  inference/
    base.py
    hf_backend.py
    vllm_backend.py
  evaluation/
    metrics.py
    qa_eval.py
    rubric_eval.py
  utils/
    cache.py
    logging.py
    text.py
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

## Prompt Design (Important)

Prompt templates are centralized in `codewiki_hpc/prompts.py`:

- Stage A (`make_stage_a_user_prompt`): per-file role/responsibility summary
- Stage B (`make_stage_b_user_prompt`): per-module synthesis from Stage A summaries
- Stage C (`make_stage_c_user_prompt`): final holistic repository documentation

In `codewiki_hpc/summarizer.py`, Stage C also includes:

- anti-copy filtering for noisy HTML/docs link blocks
- quality checks for final markdown structure
- retry with reduced prompt budget
- deterministic fallback document when generation repeatedly fails

## End-to-End Run Guide (HPC)

### 0) Environment

```bash
cd /home/22011107/TA/NLPCODEWIKI/CodeWiki
conda activate codewiki_py312
pip install -r requirements.txt
```

### 1) Gateway node (has internet): prefetch repos

```bash
cd /home/22011107/TA/NLPCODEWIKI/CodeWiki
./scripts/prefetch_gateway.sh
```

This fills `outputs/cache/repos/` so GPU nodes do not need internet.

### 2) GPU node (offline): generate docs + evaluate

```bash
cd /home/22011107/TA/NLPCODEWIKI/CodeWiki
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

### 3) Continue unfinished run (resume)

```bash
python -m codewiki_hpc.run \
  --config configs/default.yaml \
  --models "Mistral,Qwen" \
  --split train \
  --max_repos 22 \
  --backend transformers \
  --offline \
  --model_root /work/$USER/models \
  --output_dir outputs \
  --resume
```

## Command Cookbook

### Run one model only

```bash
python -m codewiki_hpc.run \
  --config configs/default.yaml \
  --models "Qwen" \
  --split train \
  --max_repos 22 \
  --backend transformers \
  --offline \
  --model_root /work/$USER/models \
  --output_dir outputs \
  --resume
```

### Regenerate only failed/unfinished rows

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

### Evaluation-only pass on existing docs

```bash
python -m codewiki_hpc.run \
  --config configs/default.yaml \
  --models "CodeLlama,DeepSeekCoder,Mistral,Qwen" \
  --split train \
  --max_repos 22 \
  --output_dir outputs \
  --eval_only \
  --resume
```

## Output Artifacts

### Benchmark CSVs

- `outputs/results/cwbench_CodeLlama-7b-Instruct-hf.csv`
- `outputs/results/cwbench_deepseek-coder-6.7b-instruct.csv`
- `outputs/results/cwbench_Mistral-7B-Instruct-v0.3.csv`
- `outputs/results/cwbench_Qwen2.5-Coder-7B-Instruct.csv`
- `outputs/results/cwbench_all_models.csv`

### Generated Documentation

- `outputs/docs/CodeLlama/*.md`
- `outputs/docs/DeepSeekCoder/*.md`
- `outputs/docs/Mistral/*.md`
- `outputs/docs/Qwen/*.md`

Sample docs:
- `outputs/docs/CodeLlama/trino.md`
- `outputs/docs/Mistral/Chart.js.md`
- `outputs/docs/Qwen/svelte.md`

## Practical Notes

- If you see many `git clone ... exit 128` errors on GPU: run prefetch on gateway first.
- If Stage C output is low-quality/copy-heavy, the pipeline auto-retries and can fallback to deterministic template synthesis.
- If interrupted (`Ctrl+C`), rerun with `--resume`.
- For this cluster, use offline mode on GPU by default.

## License

MIT (inherits upstream project licensing for integrated components).
