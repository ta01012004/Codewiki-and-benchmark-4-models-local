from __future__ import annotations

import json
from typing import Any


CHUNK_SUMMARY_SYSTEM_V2 = (
    "You are a senior software architect building grounded repository evidence. "
    "Summarize only what is supported by the provided chunk. "
    "Focus on architectural role, interfaces, dependencies, and runtime relevance."
)

SUBMODULE_SUMMARY_SYSTEM_V2 = (
    "You synthesize subsystem documentation from chunk-level evidence. "
    "Preserve cross-file interactions, entrypoints, and configuration dependencies. "
    "Do not speculate beyond the evidence."
)

SECTION_SYNTHESIS_SYSTEM_V2 = (
    "You write one section of holistic repository documentation. "
    "Use only the supplied architecture IR and evidence pack. "
    "Make the section concrete, architecture-aware, and concise. "
    "Do not output citations or bullet IDs."
)


def make_chunk_summary_prompt_v2(
    repo_name: str,
    chunk_id: str,
    file_path: str,
    chunk_kind: str,
    chunk_header: str,
    chunk_text: str,
) -> str:
    return (
        f"Repository: {repo_name}\n"
        f"Chunk ID: {chunk_id}\n"
        f"File: {file_path}\n"
        f"Kind: {chunk_kind}\n"
        f"Header: {chunk_header or 'n/a'}\n\n"
        "Task: Summarize this chunk in <=120 words.\n"
        "Include: responsibility, key APIs/objects/config keys, and what other parts of the system depend on it.\n"
        "Avoid low-level repetition and avoid inventing behavior.\n\n"
        "Chunk content:\n"
        f"```\n{chunk_text}\n```"
    )


def make_submodule_summary_prompt_v2(
    repo_name: str,
    community: dict[str, Any],
    chunk_summaries: list[dict[str, Any]],
) -> str:
    items = "\n\n".join(
        [
            f"- {item.get('file_path','unknown')} [{item.get('chunk_id','?')}]: {item.get('summary','')}"
            for item in chunk_summaries
        ]
    )
    community_blob = json.dumps(
        {
            "community_id": community.get("community_id"),
            "label": community.get("label"),
            "member_count": community.get("member_count"),
            "top_paths": community.get("top_paths", []),
            "entrypoints": community.get("entrypoints", []),
            "top_cross_edges": community.get("top_cross_edges", []),
        },
        ensure_ascii=False,
        indent=2,
    )
    return (
        f"Repository: {repo_name}\n"
        "Task: Produce a 180-260 word subsystem summary grounded in the evidence.\n"
        "Explain: purpose, internal structure, dependencies, runtime role, and likely extension points.\n\n"
        f"Community:\n{community_blob}\n\n"
        f"Evidence summaries:\n{items}"
    )


def make_section_prompt_v2(
    repo_name: str,
    section_name: str,
    section_goal: str,
    ir_excerpt: dict[str, Any],
    evidence_pack: list[dict[str, Any]],
    submodule_summaries: dict[str, str],
) -> str:
    evidence_blob = "\n\n".join(
        [
            f"[{item.get('evidence_id','?')}] {item.get('file_path','unknown')} "
            f"(score={item.get('score', 0):.3f})\n"
            f"Summary: {item.get('summary','')}\n"
            f"Snippet: {item.get('snippet','')[:700]}"
            for item in evidence_pack
        ]
    )
    submodules_blob = "\n\n".join([f"## {k}\n{v}" for k, v in submodule_summaries.items()])
    return (
        f"Repository: {repo_name}\n"
        f"Section: {section_name}\n\n"
        f"Goal: {section_goal}\n\n"
        "Requirements:\n"
        "- Ground every claim in the evidence pack or architecture IR.\n"
        "- Mention concrete modules/files when helpful.\n"
        "- Do not copy snippets verbatim.\n"
        "- Output Markdown only for this section body, not the heading.\n\n"
        f"Architecture IR excerpt:\n{json.dumps(ir_excerpt, ensure_ascii=False, indent=2)}\n\n"
        f"Subsystem summaries:\n{submodules_blob}\n\n"
        f"Evidence pack:\n{evidence_blob}"
    )
