# CodeWiki V2 Demo Runbook

This runbook collects the exact commands needed to reproduce the current CodeWiki V2 workflow on the HPC cluster.

## 1. Environment

```bash
cd /home/22011107/TA/NLPCODEWIKI/CodeWiki
source ~/miniconda3/etc/profile.d/conda.sh
conda activate codewiki_py312
export HF_DATASETS_OFFLINE=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
```

## 2. Get a GPU interactively

```bash
srun -p gpu --gres=gpu:1 --pty sh -l
```

Check that the GPU is visible:

```bash
hostname
nvidia-smi
```

## 3. Run V2 on the 7 CodeWiki paper repos

The exact repo subset is:

- `OpenHands`
- `svelte`
- `puppeteer`
- `ml-agents`
- `logstash`
- `wazuh`
- `electron`

### 3.1 Run one generation model

```bash
cd /home/22011107/TA/NLPCODEWIKI/CodeWiki
source ~/miniconda3/etc/profile.d/conda.sh
conda activate codewiki_py312
export CUDA_VISIBLE_DEVICES=0
export HF_DATASETS_OFFLINE=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

python -m codewiki_hpc.run \
  --config configs/default.yaml \
  --models "CodeLlama" \
  --repos "OpenHands,svelte,puppeteer,ml-agents,logstash,wazuh,electron" \
  --split train \
  --max_repos 22 \
  --backend transformers \
  --offline \
  --model_root /work/$USER/models \
  --output_dir outputs \
  --pipeline_version v2
```

Replace `CodeLlama` with one of:

- `DeepSeekCoder`
- `Mistral`
- `Qwen`

### 3.2 Submit as a Slurm batch job

```bash
cd /home/22011107/TA/NLPCODEWIKI/CodeWiki
sbatch -p gpu --gres=gpu:1 -J codewiki_v2_codellama_7repo \
  -o outputs/logs/codewiki_v2_codellama_7repo_%j.out \
  -e outputs/logs/codewiki_v2_codellama_7repo_%j.err \
  --wrap='source ~/miniconda3/etc/profile.d/conda.sh && conda activate codewiki_py312 && cd /home/22011107/TA/NLPCODEWIKI/CodeWiki && export CUDA_VISIBLE_DEVICES=0 HF_DATASETS_OFFLINE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 && python -m codewiki_hpc.run --config configs/default.yaml --models "CodeLlama" --repos "OpenHands,svelte,puppeteer,ml-agents,logstash,wazuh,electron" --split train --max_repos 22 --backend transformers --offline --model_root /work/$USER/models --output_dir outputs --pipeline_version v2'
```

## 4. Check job status and logs

```bash
squeue -u $USER
```

```bash
tail -f outputs/logs/codewiki_v2_codellama_7repo_<JOBID>.err
```

## 5. Result files after generation

Per-model outputs:

```bash
ls outputs/results/cwbench_*.csv
```

Main summary files:

```bash
cat outputs/results/cwbench_matrix_model.csv
cat outputs/results/cwbench_matrix_repo.csv
```

## 6. Judge the generated docs with Qwen

Use Qwen as the single fixed judge after all generation models finish.

```bash
cd /home/22011107/TA/NLPCODEWIKI/CodeWiki
source ~/miniconda3/etc/profile.d/conda.sh
conda activate codewiki_py312
export CUDA_VISIBLE_DEVICES=0
export HF_DATASETS_OFFLINE=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

python codewiki/eval_codewikibench_local \
  --judge_model /work/$USER/models/Qwen2.5-Coder-7B-Instruct \
  --docs_root outputs/docs \
  --output_csv outputs/results/judge_qwen_repo.csv \
  --split train \
  --model_keys "CodeLlama,DeepSeekCoder,Mistral,Qwen" \
  --repos "OpenHands,svelte,puppeteer,ml-agents,logstash,wazuh,electron" \
  --max_doc_chars 8000 \
  --max_leaves 80 \
  --progress_every 20 \
  --offline
```

## 7. Files to cite in reports

Pre-V2 baseline:

- `outputs/results_pre_v2_baseline/cwbench_paper_7repo_repo_matrix_pre_v2.csv`
- `outputs/results_pre_v2_baseline/cwbench_paper_7repo_model_summary_pre_v2.csv`

Current V2 outputs:

- `outputs/results/cwbench_matrix_model.csv`
- `outputs/results/cwbench_matrix_repo.csv`
- `outputs/results/judge_qwen_repo.csv`

## 8. Quick sanity checks before demo

```bash
python -m compileall codewiki_hpc
python -m codewiki_hpc.run --help
```

```bash
ls /work/$USER/models
```

```bash
test -f outputs/docs/CodeLlama/OpenHands.md && echo OK
```
