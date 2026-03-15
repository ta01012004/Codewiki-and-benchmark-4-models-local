# Pre-V2 Baseline Results

These CSV files are restored from commit `5ead480` (`feat(eval): add CodeWiki-style evaluation matrix and refresh benchmark outputs`).

Meaning:
- baseline evaluation before the later V2 graph decomposition + architecture IR + section-aware RAG improvements
- kept in a separate directory to avoid confusion with current in-progress V2 outputs under `outputs/results/`

Source commit:
- `5ead480`

Additional baseline subset file:
- `cwbench_paper_7repo_repo_matrix_pre_v2.csv`: exact 7 CodeWiki paper repos from the pre-V2 baseline (`OpenHands,svelte,puppeteer,ml-agents,logstash,wazuh,electron`).
- `cwbench_paper_7repo_model_summary_pre_v2.csv`: model-level mean metrics for the exact 7 CodeWiki paper repos in the pre-V2 baseline.
