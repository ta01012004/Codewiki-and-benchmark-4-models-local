from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .decomposition_v2 import build_architecture_ir, compact_ir_for_prompt, render_mermaid_from_ir, section_keywords
from .file_selector import select_top_k_files
from .inference.base import GenerationParams
from .prompts_v2 import (
    CHUNK_SUMMARY_SYSTEM_V2,
    SECTION_SYNTHESIS_SYSTEM_V2,
    SUBMODULE_SUMMARY_SYSTEM_V2,
    make_chunk_summary_prompt_v2,
    make_section_prompt_v2,
    make_submodule_summary_prompt_v2,
)
from .repo_manager import RepoContext
from .utils.cache import JsonCache, read_json, write_json
from .utils.text import normalize_text, read_text_safe, token_overlap_score, truncate_text


SECTION_SPECS = [
    (
        "Overview",
        "Explain what the repository does, the main problem it solves, and which subsystems dominate the design.",
    ),
    (
        "Architecture",
        "Describe major subsystems, how they are decomposed in the architecture graph, and the strongest interactions between them.",
    ),
    (
        "Data Flow / Execution Flow",
        "Explain the main runtime path from entrypoints through core services or pipelines to outputs and side effects.",
    ),
    (
        "Configuration & Dependencies",
        "Describe build files, config surfaces, important runtime dependencies, and environment assumptions inferred from the repository.",
    ),
    (
        "How to Run / Key Scripts",
        "List the most relevant commands, scripts, and entrypoints for running, building, or testing the repository.",
    ),
    (
        "Notable Design Choices / Extension Points",
        "Highlight abstractions, plugin or hook points, and design choices that matter for maintenance or extension.",
    ),
]


class RepoSummarizerV2:
    def __init__(self, cfg: dict[str, Any], logger: Any):
        self.cfg = cfg
        self.logger = logger

        v2_cfg = cfg.get("summarization_v2", {})
        fallback_cfg = cfg.get("summarization", {})
        self.top_k_files = int(v2_cfg.get("top_k_files", fallback_cfg.get("top_k_files", 36)))
        self.max_file_chars = int(v2_cfg.get("max_file_chars", fallback_cfg.get("max_file_chars", 14000)))
        self.stage_timeout_sec = int(v2_cfg.get("timeout_sec", fallback_cfg.get("timeout_sec", 180)))
        self.chunk_chars = int(v2_cfg.get("chunk_chars", 1800))
        self.chunk_overlap = int(v2_cfg.get("chunk_overlap", 240))
        self.max_chunks = int(v2_cfg.get("max_chunks", 240))
        self.max_chunk_summaries = int(v2_cfg.get("max_chunk_summaries", 72))
        self.max_submodules = int(v2_cfg.get("max_submodules", 8))
        self.max_section_evidence = int(v2_cfg.get("max_section_evidence", 8))

    @staticmethod
    def _repo_cache_dir(output_dir: Path, repo_name: str) -> Path:
        return output_dir / "cache" / repo_name

    @staticmethod
    def _split_markdown_sections(text: str) -> list[tuple[str, str]]:
        sections: list[tuple[str, str]] = []
        current_header = "document"
        current_lines: list[str] = []
        for line in text.splitlines():
            if re.match(r"^\s{0,3}#{1,6}\s+", line):
                if current_lines:
                    sections.append((current_header, "\n".join(current_lines).strip()))
                current_header = line.strip("# ").strip() or "section"
                current_lines = []
                continue
            current_lines.append(line)
        if current_lines:
            sections.append((current_header, "\n".join(current_lines).strip()))
        return [(header, body) for header, body in sections if body]

    def _chunk_file(self, item: dict[str, Any], text: str) -> list[dict[str, Any]]:
        path = item["path"]
        kind = "code"
        lower = path.lower()
        if lower.endswith((".md", ".rst", ".adoc", ".txt")):
            kind = "doc"
        elif lower.endswith((".yaml", ".yml", ".json", ".toml", ".ini")):
            kind = "config"
        elif Path(path).name in {"package.json", "requirements.txt", "pyproject.toml", "setup.py", "pom.xml", "build.gradle", "CMakeLists.txt"}:
            kind = "build"

        chunks: list[dict[str, Any]] = []
        if kind == "doc":
            sections = self._split_markdown_sections(text) or [("document", text)]
            for idx, (header, body) in enumerate(sections, start=1):
                body = truncate_text(body, self.chunk_chars)
                if not body.strip():
                    continue
                chunks.append(
                    {
                        "chunk_id": f"{path}::section::{idx}",
                        "file_path": path,
                        "kind": kind,
                        "header": header,
                        "text": body,
                        "snippet": body[:600],
                    }
                )
            return chunks

        pattern = None
        if item.get("language") in {"python", "javascript", "typescript", "java", "go", "rust"}:
            pattern = re.compile(r"(?m)^(?:class|def|async def|function|export function|export class|interface|struct|impl|fn)\b.*$")
        boundaries: list[int] = [0]
        if pattern:
            for match in pattern.finditer(text):
                boundaries.append(match.start())
        boundaries = sorted(set(boundaries))
        if len(boundaries) <= 1:
            boundaries = list(range(0, len(text), max(self.chunk_chars - self.chunk_overlap, 400)))

        for idx, start in enumerate(boundaries, start=1):
            body = text[start : start + self.chunk_chars]
            if not body.strip():
                continue
            header_line = body.splitlines()[0].strip() if body.splitlines() else Path(path).name
            chunks.append(
                {
                    "chunk_id": f"{path}::chunk::{idx}",
                    "file_path": path,
                    "kind": kind,
                    "header": truncate_text(header_line, 120),
                    "text": body,
                    "snippet": body[:600],
                }
            )
            if len(chunks) >= 12:
                break
        return chunks

    def _select_manifest_files(self, ctx: RepoContext, ir: dict[str, Any]) -> list[dict[str, Any]]:
        selected = select_top_k_files(
            files=ctx.files,
            docs_files=ctx.docs_files,
            module_map=ctx.module_map,
            top_k=self.top_k_files,
        )
        chosen = {item["path"]: item for item in selected}
        for node in ir.get("top_nodes", [])[:24]:
            path = node.get("path")
            if path and path not in chosen:
                match = next((item for item in ctx.files if item["path"] == path), None)
                if match:
                    chosen[path] = match
        return list(chosen.values())[: self.top_k_files + 16]

    def _build_chunk_manifest(self, ctx: RepoContext, ir: dict[str, Any]) -> list[dict[str, Any]]:
        docs_paths = {item["path"] for item in ctx.docs_files}
        fan_in = ctx.module_map.get("fan_in", {}) or {}
        path_to_community = {}
        for community in ir.get("communities", []):
            for member in community.get("members", []):
                path_to_community[member] = community.get("community_id")

        manifest: list[dict[str, Any]] = []
        for item in self._select_manifest_files(ctx, ir):
            text = read_text_safe(item["abs_path"], max_chars=self.max_file_chars)
            text = truncate_text(text, self.max_file_chars)
            if not text.strip():
                continue
            file_chunks = self._chunk_file(item, text)
            for chunk in file_chunks:
                base_score = 1.0 + min(float(fan_in.get(item["path"], 0)), 20.0) * 0.15
                if item["path"] in docs_paths:
                    base_score += 2.0
                if item["path"] in (ctx.module_map.get("entrypoints", []) or []):
                    base_score += 3.0
                if item["path"] in (ctx.module_map.get("build_files", []) or []):
                    base_score += 2.5
                if item["path"] in (ctx.module_map.get("configs", []) or []):
                    base_score += 2.0
                chunk["community_id"] = path_to_community.get(item["path"])
                chunk["score"] = round(base_score, 4)
                manifest.append(chunk)

        manifest.sort(key=lambda item: (-float(item.get("score", 0.0)), item["chunk_id"]))
        return manifest[: self.max_chunks]

    def _build_retrieval_index(self, manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
        index: list[dict[str, Any]] = []
        for chunk in manifest:
            tokens = normalize_text(" ".join([chunk["file_path"], chunk.get("header", ""), chunk.get("text", "")[:1200]])).split()
            counts = Counter(tokens)
            keywords = [token for token, _ in counts.most_common(24) if len(token) > 2]
            index.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "file_path": chunk["file_path"],
                    "kind": chunk["kind"],
                    "community_id": chunk.get("community_id"),
                    "score": chunk.get("score", 0.0),
                    "keywords": keywords,
                    "snippet": chunk.get("snippet", ""),
                    "header": chunk.get("header", ""),
                }
            )
        return index

    @staticmethod
    def _index_map(index: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {item["chunk_id"]: item for item in index}

    def _score_chunk_for_section(
        self,
        section_name: str,
        chunk: dict[str, Any],
        submodule_map: dict[str, str],
        entrypoints: set[str],
        configs: set[str],
        build_files: set[str],
        docs_paths: set[str],
    ) -> float:
        keywords = section_keywords(section_name)
        chunk_text = " ".join(
            [
                chunk["file_path"],
                chunk.get("header", ""),
                chunk.get("summary", ""),
                chunk.get("snippet", ""),
                submodule_map.get(chunk.get("community_id", ""), ""),
            ]
        )
        lexical = max(token_overlap_score(" ".join(keywords), chunk_text), 0.0)
        score = float(chunk.get("score", 0.0)) + lexical * 8.0
        path = chunk["file_path"]
        if section_name == "Overview" and (path in entrypoints or path in docs_paths):
            score += 3.0
        if section_name == "Architecture" and chunk.get("community_id"):
            score += 2.0
        if section_name == "Data Flow / Execution Flow" and path in entrypoints:
            score += 4.0
        if section_name == "Configuration & Dependencies" and (path in configs or path in build_files):
            score += 4.0
        if section_name == "How to Run / Key Scripts" and (path in build_files or path in docs_paths or path in entrypoints):
            score += 4.0
        if section_name == "Notable Design Choices / Extension Points" and any(
            token in path.lower() for token in ["plugin", "hook", "interface", "extension", "registry"]
        ):
            score += 4.0
        return score

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

    def _build_fallback_doc(
        self,
        ctx: RepoContext,
        ir: dict[str, Any],
        section_evidence: dict[str, list[dict[str, Any]]],
        submodule_summaries: dict[str, str],
    ) -> str:
        mermaid = render_mermaid_from_ir(ir)
        top_interactions = ir.get("top_cross_community_interactions", [])[:6]
        interaction_lines = [
            f"- `{edge.get('source_community')}` -> `{edge.get('target_community')}` (weight={edge.get('weight')})"
            for edge in top_interactions
        ] or ["- Cross-community dependencies were weak or sparsely detected."]
        top_submodules = [
            f"- `{community_id}`: {self._first_sentence(summary)}"
            for community_id, summary in list(submodule_summaries.items())[:8]
        ] or ["- No subsystem summaries were available."]

        run_items = [
            item.get("file_path", "unknown")
            for item in section_evidence.get("How to Run / Key Scripts", [])[:8]
        ]
        config_items = [
            item.get("file_path", "unknown")
            for item in section_evidence.get("Configuration & Dependencies", [])[:8]
        ]
        flow_paths = [
            item.get("file_path", "unknown")
            for item in section_evidence.get("Data Flow / Execution Flow", [])[:8]
        ]

        return (
            "# Overview\n"
            f"Repository `{ctx.sample.repo_name}` at commit `{ctx.sample.commit_id}` is documented using a graph-aware V2 pipeline. "
            f"The architecture IR contains {ir.get('node_count', 0)} nodes, {ir.get('edge_count', 0)} edges, and "
            f"{len(ir.get('communities', []))} detected subsystems.\n\n"
            "## Architecture\n"
            "The repository was decomposed into communities derived from dependency and package signals.\n"
            "Top subsystems:\n"
            f"{chr(10).join(top_submodules)}\n\n"
            "### Architecture Graph\n"
            "```mermaid\n"
            f"{mermaid}\n"
            "```\n\n"
            "Top cross-community interactions:\n"
            f"{chr(10).join(interaction_lines)}\n\n"
            "## Data Flow / Execution Flow\n"
            f"Evidence suggests execution moves across: {', '.join(flow_paths) if flow_paths else 'entrypoints and core subsystems'}.\n"
            "Entrypoints hand off to subsystem-specific modules discovered in the architecture graph before producing outputs or side effects.\n\n"
            "## Configuration & Dependencies\n"
            f"- Build/dependency files: {', '.join(ir.get('build_files', [])[:12]) or 'not clearly detected'}\n"
            f"- Config surfaces: {', '.join(config_items) if config_items else 'not clearly detected'}\n"
            f"- Docs anchors: {', '.join(ir.get('docs_anchors', [])[:8]) or 'not clearly detected'}\n\n"
            "## How to Run / Key Scripts\n"
            f"- Entrypoints: {', '.join(ir.get('entrypoints', [])[:12]) or 'not clearly detected'}\n"
            f"- Likely operational scripts/docs: {', '.join(run_items) if run_items else 'not clearly detected'}\n\n"
            "## Notable Design Choices / Extension Points\n"
            "- V2 graph decomposition highlights subsystem boundaries using import, package, and configuration signals.\n"
            "- Extension points are typically concentrated around high-centrality files, registries, interfaces, and build/config entry surfaces.\n"
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
        repo_cache_dir.mkdir(parents=True, exist_ok=True)
        model_cache_dir = repo_cache_dir / model_key
        model_cache_dir.mkdir(parents=True, exist_ok=True)

        ir_path = repo_cache_dir / "architecture_ir.json"
        if resume and ir_path.exists():
            ir = read_json(ir_path)
        else:
            ir = build_architecture_ir(ctx)
            write_json(ir_path, ir)

        manifest_path = repo_cache_dir / "chunk_manifest.json"
        index_path = repo_cache_dir / "retrieval_index.json"
        if resume and manifest_path.exists() and index_path.exists():
            manifest = read_json(manifest_path).get("chunks", [])
            retrieval_index = read_json(index_path).get("chunks", [])
        else:
            manifest = self._build_chunk_manifest(ctx, ir)
            retrieval_index = self._build_retrieval_index(manifest)
            write_json(manifest_path, {"chunks": manifest})
            write_json(index_path, {"chunks": retrieval_index})

        chunk_cache = JsonCache(model_cache_dir / "stage_a_chunk_summaries.json")
        submodule_cache = JsonCache(model_cache_dir / "stage_b_submodule_summaries.json")
        section_evidence_cache = JsonCache(model_cache_dir / "section_evidence.json")
        section_text_cache = JsonCache(model_cache_dir / "stage_c_sections.json")

        params = GenerationParams(
            max_new_tokens=int(model_gen_cfg.get("max_new_tokens", 512)),
            temperature=float(model_gen_cfg.get("temperature", 0.1)),
            top_p=float(model_gen_cfg.get("top_p", 0.9)),
            timeout_sec=int(model_gen_cfg.get("timeout_sec", self.stage_timeout_sec)),
        )

        selected_chunks = manifest[: self.max_chunk_summaries]
        chunk_summaries: dict[str, dict[str, Any]] = {}
        total_chunks = len(selected_chunks)
        self.logger.info(
            f"[{ctx.sample.repo_name}] V2 stage A start: chunks={total_chunks} max_new_tokens={params.max_new_tokens}"
        )
        for idx, chunk in enumerate(selected_chunks, start=1):
            cached = chunk_cache.get(chunk["chunk_id"])
            if resume and isinstance(cached, dict) and str(cached.get("summary", "")).strip():
                chunk_summaries[chunk["chunk_id"]] = cached
                self.logger.info(f"[{ctx.sample.repo_name}] V2 stage A {idx}/{total_chunks} cache-hit: {chunk['chunk_id']}")
                continue
            prompt = make_chunk_summary_prompt_v2(
                repo_name=ctx.sample.repo_name,
                chunk_id=chunk["chunk_id"],
                file_path=chunk["file_path"],
                chunk_kind=chunk["kind"],
                chunk_header=chunk.get("header", ""),
                chunk_text=chunk["text"],
            )
            try:
                summary = backend.generate(CHUNK_SUMMARY_SYSTEM_V2, prompt, params).strip()
            except Exception as e:
                summary = f"[stage-a-v2-failed] {e}"
            payload = {
                "chunk_id": chunk["chunk_id"],
                "file_path": chunk["file_path"],
                "kind": chunk["kind"],
                "community_id": chunk.get("community_id"),
                "summary": summary,
                "score": chunk.get("score", 0.0),
                "snippet": chunk.get("snippet", ""),
                "header": chunk.get("header", ""),
            }
            chunk_summaries[chunk["chunk_id"]] = payload
            chunk_cache.set(chunk["chunk_id"], payload)
            chunk_cache.save()

        self.logger.info(f"[{ctx.sample.repo_name}] V2 stage A done: chunk_summaries={len(chunk_summaries)}")

        community_items = sorted(
            ir.get("communities", []),
            key=lambda item: (-float(item.get("importance", 0.0)), item.get("community_id", "")),
        )[: self.max_submodules]
        submodule_summaries: dict[str, str] = {}
        total_communities = len(community_items)
        self.logger.info(f"[{ctx.sample.repo_name}] V2 stage B start: communities={total_communities}")
        for idx, community in enumerate(community_items, start=1):
            community_id = community["community_id"]
            cached = submodule_cache.get(community_id)
            if resume and isinstance(cached, str) and cached.strip():
                submodule_summaries[community_id] = cached
                self.logger.info(f"[{ctx.sample.repo_name}] V2 stage B {idx}/{total_communities} cache-hit: {community_id}")
                continue
            community_chunk_summaries = [
                payload
                for payload in chunk_summaries.values()
                if payload.get("community_id") == community_id
            ]
            community_chunk_summaries.sort(key=lambda item: (-float(item.get("score", 0.0)), item.get("chunk_id", "")))
            prompt = make_submodule_summary_prompt_v2(
                repo_name=ctx.sample.repo_name,
                community=community,
                chunk_summaries=community_chunk_summaries[:10],
            )
            try:
                summary = backend.generate(SUBMODULE_SUMMARY_SYSTEM_V2, prompt, params).strip()
            except Exception as e:
                summary = f"[stage-b-v2-failed] {e}"
            submodule_summaries[community_id] = summary
            submodule_cache.set(community_id, summary)
            submodule_cache.save()

        self.logger.info(f"[{ctx.sample.repo_name}] V2 stage B done: submodules={len(submodule_summaries)}")

        entrypoints = set(ctx.module_map.get("entrypoints", []) or [])
        configs = set(ctx.module_map.get("configs", []) or [])
        build_files = set(ctx.module_map.get("build_files", []) or [])
        docs_paths = {item["path"] for item in ctx.docs_files}
        section_evidence: dict[str, list[dict[str, Any]]] = {}
        section_texts: dict[str, str] = {}
        submodule_map = dict(submodule_summaries)

        self.logger.info(f"[{ctx.sample.repo_name}] V2 stage C start: sections={len(SECTION_SPECS)}")
        for section_name, section_goal in SECTION_SPECS:
            cached_evidence = section_evidence_cache.get(section_name)
            cached_text = section_text_cache.get(section_name)
            if (
                resume
                and isinstance(cached_evidence, list)
                and cached_evidence
                and isinstance(cached_text, str)
                and cached_text.strip()
            ):
                section_evidence[section_name] = cached_evidence
                section_texts[section_name] = cached_text
                self.logger.info(f"[{ctx.sample.repo_name}] V2 stage C cache-hit: section={section_name}")
                continue

            ranked_evidence = []
            for chunk in selected_chunks:
                payload = chunk_summaries.get(chunk["chunk_id"])
                if payload is None:
                    continue
                score = self._score_chunk_for_section(
                    section_name=section_name,
                    chunk=payload,
                    submodule_map=submodule_map,
                    entrypoints=entrypoints,
                    configs=configs,
                    build_files=build_files,
                    docs_paths=docs_paths,
                )
                ranked_evidence.append((score, payload))
            ranked_evidence.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
            evidence_pack = []
            for score, payload in ranked_evidence[: self.max_section_evidence]:
                evidence_pack.append(
                    {
                        "evidence_id": payload["chunk_id"],
                        "file_path": payload["file_path"],
                        "summary": payload["summary"],
                        "snippet": truncate_text(payload.get("snippet", ""), 500),
                        "score": round(score, 4),
                    }
                )
            section_evidence[section_name] = evidence_pack
            section_evidence_cache.set(section_name, evidence_pack)
            section_evidence_cache.save()

            ir_excerpt = compact_ir_for_prompt(ir)
            prompt = make_section_prompt_v2(
                repo_name=ctx.sample.repo_name,
                section_name=section_name,
                section_goal=section_goal,
                ir_excerpt=ir_excerpt,
                evidence_pack=evidence_pack,
                submodule_summaries=submodule_summaries,
            )
            section_params = GenerationParams(
                max_new_tokens=max(192, min(512, params.max_new_tokens)),
                temperature=params.temperature,
                top_p=params.top_p,
                timeout_sec=params.timeout_sec,
            )
            try:
                section_body = backend.generate(SECTION_SYNTHESIS_SYSTEM_V2, prompt, section_params).strip()
            except Exception as e:
                section_body = f"[stage-c-v2-failed] {e}"
            section_texts[section_name] = section_body
            section_text_cache.set(section_name, section_body)
            section_text_cache.save()

        mermaid = render_mermaid_from_ir(ir)
        doc_parts = []
        architecture_inserted = False
        for section_name, _ in SECTION_SPECS:
            body = section_texts.get(section_name, "").strip()
            if not body or body.startswith("[stage-c-v2-failed]"):
                continue
            heading = "# Overview" if section_name == "Overview" else f"## {section_name}"
            doc_parts.append(heading)
            doc_parts.append(body)
            if section_name == "Architecture":
                doc_parts.append("### Architecture Graph")
                doc_parts.append("```mermaid")
                doc_parts.append(mermaid)
                doc_parts.append("```")
                architecture_inserted = True

        if not doc_parts:
            doc_text = self._build_fallback_doc(ctx, ir, section_evidence, submodule_summaries)
        else:
            doc_text = "\n\n".join(doc_parts).strip()
            if not architecture_inserted:
                doc_text += f"\n\n## Architecture\n\n### Architecture Graph\n```mermaid\n{mermaid}\n```"

        docs_dir = output_dir / "docs" / model_key
        docs_dir.mkdir(parents=True, exist_ok=True)
        doc_path = docs_dir / f"{ctx.sample.repo_name}.md"
        doc_path.write_text(doc_text, encoding="utf-8")

        elapsed = time.time() - start
        self.logger.info(f"[{ctx.sample.repo_name}] V2 done: doc={doc_path} elapsed_sec={elapsed:.2f}")
        return {
            "doc_path": str(doc_path),
            "generation_time_sec": round(elapsed, 3),
            "chunk_count": len(manifest),
            "chunk_summary_count": len(chunk_summaries),
            "submodule_summary_count": len(submodule_summaries),
            "community_count": len(ir.get("communities", [])),
            "architecture_ir_path": str(ir_path),
        }
