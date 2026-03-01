from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .file_selector import group_file_summaries_by_module, select_top_k_files
from .inference.base import GenerationParams
from .prompts import (
    STAGE_A_SYSTEM,
    STAGE_B_SYSTEM,
    STAGE_C_SYSTEM,
    make_stage_a_user_prompt,
    make_stage_b_user_prompt,
    make_stage_c_user_prompt,
)
from .repo_manager import RepoContext
from .utils.cache import JsonCache
from .utils.text import read_text_safe, truncate_text


class RepoSummarizer:
    def __init__(self, cfg: dict[str, Any], logger: Any):
        self.cfg = cfg
        self.logger = logger

        s_cfg = cfg.get("summarization", {})
        self.top_k_files = int(s_cfg.get("top_k_files", 32))
        self.max_file_chars = int(s_cfg.get("max_file_chars", 14000))
        self.stage_timeout_sec = int(s_cfg.get("timeout_sec", 180))

    @staticmethod
    def _repo_cache_dir(output_dir: Path, repo_name: str) -> Path:
        return output_dir / "cache" / repo_name

    @staticmethod
    def _sanitize_hint_text(text: str, max_chars: int = 900) -> str:
        # Drop common non-architectural noise from README/docs snippets.
        lines = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            lower = line.lower()
            if "<a " in lower or "<img " in lower or "<p " in lower:
                continue
            if "http://" in lower or "https://" in lower:
                continue
            if "•" in line:
                continue
            if re.match(r"^!\[.*\]\(.*\)$", line):
                continue
            lines.append(line)
        cleaned = "\n".join(lines)
        return truncate_text(cleaned, max_chars).strip()

    @staticmethod
    def _is_bad_stage_c_output(text: str) -> bool:
        if not text or not text.strip():
            return True
        lower = text.lower()

        # Common bad pattern: copied docs navigation html.
        if "<a href=" in lower or "<p align=" in lower or "<img" in lower:
            return True
        if lower.count("https://") >= 8:
            return True

        required_markers = [
            "overview",
            "architecture",
            "data flow",
            "configuration",
            "how to run",
            "extension",
        ]
        score = sum(1 for m in required_markers if m in lower)
        if score < 4:
            return True
        if "```mermaid" not in lower:
            return True
        return False

    @staticmethod
    def _first_sentence(text: str, max_len: int = 220) -> str:
        t = text.strip().replace("\n", " ")
        if not t:
            return "No summary available."
        for sep in [". ", "\n", "; "]:
            if sep in t:
                t = t.split(sep, 1)[0]
                break
        t = t.strip()
        return t[:max_len].rstrip() + ("..." if len(t) > max_len else "")

    def _build_fallback_doc(self, ctx: RepoContext, module_summaries: dict[str, str]) -> str:
        module_lines = []
        for name, summary in module_summaries.items():
            module_lines.append(f"- `{name}`: {self._first_sentence(summary)}")
        if not module_lines:
            module_lines.append("- No module summaries were available.")

        entrypoints = ctx.module_map.get("entrypoints", []) or []
        build_files = ctx.module_map.get("build_files", []) or []
        configs = ctx.module_map.get("configs", []) or []

        flow_path = " -> ".join(["Entrypoint", "Core Modules", "Runtime Services", "Output/Side Effects"])
        if not flow_path.strip():
            flow_path = "Entrypoint -> Core Modules -> Output"

        mermaid_nodes = ["flowchart TD", "  E[Entrypoints]", "  C[Core Modules]", "  O[Outputs]"]
        mermaid_nodes.append("  E --> C")
        mermaid_nodes.append("  C --> O")

        return (
            "# Overview\n"
            f"Repository `{ctx.sample.repo_name}` appears to implement a modular system with "
            f"{ctx.module_map.get('file_count', 'unknown')} source files at commit `{ctx.sample.commit_id}`.\n\n"
            "## Architecture\n"
            "Major components inferred from file/module analysis:\n"
            f"{chr(10).join(module_lines)}\n\n"
            "```mermaid\n"
            f"{chr(10).join(mermaid_nodes)}\n"
            "```\n\n"
            "## Data Flow / Execution Flow\n"
            f"Typical execution path: `{flow_path}`.\n"
            "Entrypoints initialize core modules, which orchestrate processing and emit outputs or side effects.\n\n"
            "## Configuration & Dependencies\n"
            f"- Build/dependency files: {', '.join(build_files) if build_files else 'not clearly detected'}\n"
            f"- Config files: {', '.join(configs) if configs else 'not clearly detected'}\n\n"
            "## How to Run / Key Scripts\n"
            f"- Detected entrypoints: {', '.join(entrypoints) if entrypoints else 'not clearly detected'}\n"
            "- Use repository build scripts/package manager tasks based on detected build files.\n\n"
            "## Notable Design Choices / Extension Points\n"
            "- The codebase is organized in modules that can be extended by adding new feature files under existing module boundaries.\n"
            "- Extension is likely centered around entrypoint wiring, configuration files, and module-specific implementations.\n"
        )

    def summarize_repo(
        self,
        ctx: RepoContext,
        model_key: str,
        backend: Any,
        model_gen_cfg: dict[str, Any],
        output_dir: Path,
        resume: bool = True,
    ) -> dict[str, Any]:
        start = time.time()

        repo_cache_dir = self._repo_cache_dir(output_dir, ctx.sample.repo_name)
        model_cache_dir = repo_cache_dir / model_key
        model_cache_dir.mkdir(parents=True, exist_ok=True)

        file_cache = JsonCache(model_cache_dir / "stage_a_file_summaries.json")
        module_cache = JsonCache(model_cache_dir / "stage_b_module_summaries.json")

        selected_files = select_top_k_files(
            files=ctx.files,
            docs_files=ctx.docs_files,
            module_map=ctx.module_map,
            top_k=self.top_k_files,
        )

        params = GenerationParams(
            max_new_tokens=int(model_gen_cfg.get("max_new_tokens", 512)),
            temperature=float(model_gen_cfg.get("temperature", 0.1)),
            top_p=float(model_gen_cfg.get("top_p", 0.9)),
            timeout_sec=int(model_gen_cfg.get("timeout_sec", self.stage_timeout_sec)),
        )

        self.logger.info(
            f"[{ctx.sample.repo_name}] stage A start: selected_files={len(selected_files)} "
            f"max_new_tokens={params.max_new_tokens} timeout_sec={params.timeout_sec}"
        )
        file_summaries: dict[str, str] = {}
        total_files = len(selected_files)
        for idx, item in enumerate(selected_files, start=1):
            path = item["path"]
            cached = file_cache.get(path)
            if resume and isinstance(cached, str) and cached.strip():
                file_summaries[path] = cached
                self.logger.info(f"[{ctx.sample.repo_name}] stage A {idx}/{total_files} cache-hit: {path}")
                continue
            self.logger.info(f"[{ctx.sample.repo_name}] stage A {idx}/{total_files} generating: {path}")

            text = read_text_safe(item["abs_path"], max_chars=self.max_file_chars)
            text = truncate_text(text, self.max_file_chars)

            user_prompt = make_stage_a_user_prompt(
                repo_name=ctx.sample.repo_name,
                file_path=path,
                file_text=text,
            )
            try:
                summary = backend.generate(STAGE_A_SYSTEM, user_prompt, params).strip()
            except Exception as e:
                summary = f"[stage-a-failed] {e}"

            file_summaries[path] = summary
            file_cache.set(path, summary)
            file_cache.save()
        self.logger.info(f"[{ctx.sample.repo_name}] stage A done: summaries={len(file_summaries)}")

        grouped = group_file_summaries_by_module(file_summaries)
        module_summaries: dict[str, str] = {}
        self.logger.info(f"[{ctx.sample.repo_name}] stage B start: modules={len(grouped)}")

        total_modules = len(grouped)
        for midx, (module_name, items) in enumerate(grouped.items(), start=1):
            cached = module_cache.get(module_name)
            if resume and isinstance(cached, str) and cached.strip():
                module_summaries[module_name] = cached
                self.logger.info(
                    f"[{ctx.sample.repo_name}] stage B {midx}/{total_modules} cache-hit: module={module_name}"
                )
                continue
            self.logger.info(
                f"[{ctx.sample.repo_name}] stage B {midx}/{total_modules} generating: module={module_name}"
            )

            user_prompt = make_stage_b_user_prompt(
                repo_name=ctx.sample.repo_name,
                module_name=module_name,
                file_summaries=items,
            )
            try:
                summary = backend.generate(STAGE_B_SYSTEM, user_prompt, params).strip()
            except Exception as e:
                summary = f"[stage-b-failed] {e}"

            module_summaries[module_name] = summary
            module_cache.set(module_name, summary)
            module_cache.save()
        self.logger.info(f"[{ctx.sample.repo_name}] stage B done: module_summaries={len(module_summaries)}")

        self.logger.info(f"[{ctx.sample.repo_name}] stage C start: holistic synthesis")
        doc_text = ""
        last_stage_c_error: Exception | None = None
        sanitized_docs_hints = []
        for hint in ctx.docs_files:
            cleaned = self._sanitize_hint_text(hint.get("text", ""))
            if cleaned:
                sanitized_docs_hints.append({"path": hint.get("path", "unknown"), "text": cleaned})

        docs_caps = [6, 4, 2, 1]
        module_char_caps = [1600, 1100, 800, 500]
        token_caps = [
            int(params.max_new_tokens),
            max(256, int(params.max_new_tokens * 0.6)),
            192,
            128,
        ]
        for attempt in range(4):
            docs_cap = docs_caps[min(attempt, len(docs_caps) - 1)]
            module_cap = module_char_caps[min(attempt, len(module_char_caps) - 1)]
            token_cap = token_caps[min(attempt, len(token_caps) - 1)]
            reduced_module_summaries = {
                k: truncate_text(v, module_cap) for k, v in module_summaries.items()
            }
            final_prompt = make_stage_c_user_prompt(
                repo_name=ctx.sample.repo_name,
                repo_url=ctx.sample.repo_url,
                commit_id=ctx.sample.commit_id,
                module_map=ctx.module_map,
                docs_hints=sanitized_docs_hints[:docs_cap],
                module_summaries=reduced_module_summaries,
            )
            stage_c_params = GenerationParams(
                max_new_tokens=token_cap,
                temperature=params.temperature,
                top_p=params.top_p,
                timeout_sec=params.timeout_sec,
            )
            try:
                self.logger.info(
                    f"[{ctx.sample.repo_name}] stage C attempt={attempt + 1}/4 "
                    f"docs_hints={docs_cap} module_char_cap={module_cap} max_new_tokens={token_cap}"
                )
                doc_text = backend.generate(STAGE_C_SYSTEM, final_prompt, stage_c_params).strip()
                if doc_text and self._is_bad_stage_c_output(doc_text):
                    self.logger.warning(
                        f"[{ctx.sample.repo_name}] stage C attempt={attempt + 1} produced low-quality/copy-heavy output; retrying."
                    )
                    doc_text = ""
                if doc_text:
                    break
            except Exception as e:
                last_stage_c_error = e
                self.logger.warning(
                    f"[{ctx.sample.repo_name}] stage C attempt={attempt + 1} failed: {type(e).__name__}: {e}"
                )
                continue

        if not doc_text:
            self.logger.warning(
                f"[{ctx.sample.repo_name}] stage C fallback: using deterministic template due to repeated generation failures."
            )
            doc_text = self._build_fallback_doc(ctx, module_summaries)

        docs_dir = output_dir / "docs" / model_key
        docs_dir.mkdir(parents=True, exist_ok=True)
        doc_path = docs_dir / f"{ctx.sample.repo_name}.md"
        doc_path.write_text(doc_text, encoding="utf-8")

        elapsed = time.time() - start
        self.logger.info(
            f"[{ctx.sample.repo_name}] stage C done: doc={doc_path} elapsed_sec={elapsed:.2f}"
        )
        return {
            "doc_path": str(doc_path),
            "generation_time_sec": round(elapsed, 3),
            "selected_file_count": len(selected_files),
            "file_summary_count": len(file_summaries),
            "module_summary_count": len(module_summaries),
        }
