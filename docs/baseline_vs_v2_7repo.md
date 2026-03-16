# Pre-V2 Baseline vs V2 Improved (7 CodeWiki Repos)

This note compares the exact 7 CodeWiki paper repos across the pre-V2 baseline and the improved V2 pipeline.

Repo subset: `OpenHands`, `svelte`, `puppeteer`, `ml-agents`, `logstash`, `wazuh`, `electron`

## Source Files

- Pre-V2 baseline model summary: `outputs/results_pre_v2_baseline/cwbench_paper_7repo_model_summary_pre_v2.csv`
- V2 improved model summary: `outputs/results_v2_improved_7repo/cwbench_matrix_model_v2_7repo.csv`
- V2 Qwen-judge summary: `outputs/results_v2_improved_7repo/judge_qwen_summary_v2_7repo.csv`
- Machine-readable comparison: `outputs/results_v2_improved_7repo/baseline_vs_v2_7repo.csv`

## Main Takeaways

- V2 improves `coverage_score` for all 4 models.
- V2 improves `factual_grounding` and `coherence` very strongly across all 4 models.
- V2 does not improve every metric uniformly: `hierarchy_alignment`, `leaf_coverage`, and some `actionability` values drop for some models.
- Under the V2 heuristic matrix, `Qwen` ranks first by `mean_quality_score`.
- Under Qwen-as-judge, `DeepSeekCoder` ranks first by `mean_weighted_quality_percent`.

## V2 Ranking By Heuristic Quality

| Rank | Model | Mean Quality | Mean Coverage |
|---|---|---:|---:|
| 1 | Qwen/Qwen2.5-Coder-7B-Instruct | 0.847154 | 0.807489 |
| 2 | mistralai/Mistral-7B-Instruct-v0.3 | 0.840438 | 0.788581 |
| 3 | meta-llama/CodeLlama-7b-Instruct-hf | 0.820851 | 0.807529 |
| 4 | deepseek-ai/deepseek-coder-6.7b-instruct | 0.820603 | 0.808555 |

## V2 Ranking By Qwen Judge

| Rank | Model | Judge Weighted Quality (%) | Judge Coverage (%) |
|---|---|---:|---:|
| 1 | deepseek-ai/deepseek-coder-6.7b-instruct | 12.06 | 15.76 |
| 2 | mistralai/Mistral-7B-Instruct-v0.3 | 9.62 | 9.91 |
| 3 | Qwen/Qwen2.5-Coder-7B-Instruct | 8.20 | 10.30 |
| 4 | meta-llama/CodeLlama-7b-Instruct-hf | 7.82 | 9.60 |

## Baseline vs V2 Deltas

| Model | Coverage Δ | Factual Δ | Coherence Δ | Actionability Δ | Notes |
|---|---:|---:|---:|---:|---|
| Qwen/Qwen2.5-Coder-7B-Instruct | +0.080740 | +0.277507 | +0.054286 | +0.071428 | key-term coverage down |
| deepseek-ai/deepseek-coder-6.7b-instruct | +0.055673 | +0.317318 | +0.064881 | +0.047618 | hierarchy down, leaf coverage down |
| meta-llama/CodeLlama-7b-Instruct-hf | +0.061424 | +0.381939 | +0.062468 | +0.023810 | hierarchy down, leaf coverage down, key-term coverage down |
| mistralai/Mistral-7B-Instruct-v0.3 | +0.070450 | +0.451865 | +0.061783 | -0.261905 | key-term coverage down |

## Interpretation

- The pre-V2 baseline was already strong on structure-oriented metrics, so large gains were not expected everywhere.
- The strongest V2 gains are in content quality indicators, especially factual grounding and coherence.
- The split between heuristic ranking and Qwen-judge ranking is useful: it suggests the improved pipeline changes how models trade off structure versus judged usefulness.

## Recommended Reporting Framing

- `Pre-V2 baseline`: the evaluation snapshot before graph decomposition + architecture IR + section-aware RAG.
- `V2 improved`: the pipeline with graph/hierarchical decomposition, architecture IR, retrieval-guided section synthesis, and hierarchical summarization.
- `Qwen judge`: the fixed open-source judge used after generation to compare the 4 models on the same 7-repo subset.
