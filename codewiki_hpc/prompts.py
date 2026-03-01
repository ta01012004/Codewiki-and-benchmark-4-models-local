from __future__ import annotations

import json
from typing import Any


STAGE_A_SYSTEM = (
    "You are a senior software architect writing high-level repository documentation. "
    "Focus on role and responsibilities, not line-by-line behavior. "
    "Ground your answer strictly in the provided file content."
)

STAGE_B_SYSTEM = (
    "You are synthesizing module-level architecture from file summaries. "
    "Preserve technical accuracy, highlight interactions, and avoid speculation."
)

STAGE_C_SYSTEM = (
    "You generate a CodeWiki-style bird's-eye repository document. "
    "Explain architecture, data flow, configuration, and extension points. "
    "Include a Mermaid architecture diagram block. "
    "Do not copy HTML blocks, navigation link lists, badges, or marketing text from README/docs. "
    "Output only technical Markdown sections."
)


def make_stage_a_user_prompt(repo_name: str, file_path: str, file_text: str) -> str:
    return (
        f"Repository: {repo_name}\n"
        f"File: {file_path}\n\n"
        "Task: Summarize this file's role and responsibilities in <=150 words.\n"
        "Include: primary purpose, key abstractions/functions/classes, and how other files likely use it.\n"
        "Avoid low-level implementation detail and avoid inventing missing behavior.\n\n"
        "File content:\n"
        f"```\n{file_text}\n```"
    )


def make_stage_b_user_prompt(repo_name: str, module_name: str, file_summaries: list[tuple[str, str]]) -> str:
    fs = "\n\n".join([f"- {p}: {s}" for p, s in file_summaries])
    return (
        f"Repository: {repo_name}\n"
        f"Module: {module_name}\n\n"
        "Task: Produce a concise module-level summary (150-220 words).\n"
        "Explain: module responsibilities, internal structure, and interactions with other modules.\n"
        "Mention important files explicitly.\n\n"
        f"File summaries:\n{fs}"
    )


def make_stage_c_user_prompt(
    repo_name: str,
    repo_url: str,
    commit_id: str,
    module_map: dict[str, Any],
    docs_hints: list[dict[str, str]],
    module_summaries: dict[str, str],
) -> str:
    hints_blob = "\n\n".join([f"[{d['path']}]\n{d['text'][:1200]}" for d in docs_hints[:6]])
    modules_blob = "\n\n".join([f"## {k}\n{v}" for k, v in module_summaries.items()])
    compact_map = {
        "entrypoints": module_map.get("entrypoints", []),
        "build_files": module_map.get("build_files", []),
        "configs": module_map.get("configs", []),
        "file_count": module_map.get("file_count"),
    }

    return (
        f"Repository: {repo_name}\n"
        f"URL: {repo_url}\n"
        f"Commit: {commit_id}\n\n"
        "Task: Write final holistic documentation in Markdown with these exact sections:\n"
        "1) Overview\n"
        "2) Architecture\n"
        "3) Data Flow / Execution Flow\n"
        "4) Configuration & Dependencies\n"
        "5) How to Run / Key Scripts\n"
        "6) Notable Design Choices / Extension Points\n\n"
        "Requirements:\n"
        "- Provide a text-based architecture diagram using Mermaid in a fenced code block.\n"
        "- Reflect cross-module interactions; do not repeat file summaries verbatim.\n"
        "- Do not copy raw HTML tags (<p>, <a>, <img>) or docs navigation links.\n"
        "- Start with '# Overview' and keep section headers as markdown headings.\n"
        "- Mention caveats where repository evidence is incomplete.\n\n"
        f"Module map snapshot:\n{json.dumps(compact_map, ensure_ascii=False, indent=2)}\n\n"
        f"Module summaries:\n{modules_blob}\n\n"
        f"Existing docs hints:\n{hints_blob}"
    )
