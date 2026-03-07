from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .repo_manager import RepoContext
from .utils.text import normalize_text


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _path_tokens(path: str) -> list[str]:
    text = path.replace("\\", "/").replace(".", "/").replace("-", "/").replace("_", "/")
    return [t for t in text.lower().split("/") if t]


def _node_importance(path: str, fan_in: int, entrypoints: set[str], build_files: set[str], configs: set[str], docs: set[str]) -> float:
    score = 1.0 + min(float(fan_in), 25.0) * 0.5
    name = Path(path).name.lower()
    if path in entrypoints:
        score += 8.0
    if path in build_files:
        score += 6.0
    if path in configs:
        score += 5.0
    if path in docs:
        score += 4.0
    if name.startswith("readme"):
        score += 2.0
    if any(tok in path.lower() for tok in ["core", "engine", "service", "runtime", "pipeline", "plugin", "extension"]):
        score += 2.0
    return round(score, 4)


def _resolve_import_target(target: str, file_paths: set[str], stem_to_path: dict[str, str]) -> str | None:
    normalized = target.replace(".", "/").strip().lstrip("./")
    candidates = [
        normalized,
        f"{normalized}.py",
        f"{normalized}.ts",
        f"{normalized}.tsx",
        f"{normalized}.js",
        f"{normalized}.java",
        f"{normalized}.go",
        f"{normalized}.rs",
        f"{normalized}.cpp",
        f"{normalized}.h",
    ]
    for candidate in candidates:
        if candidate in file_paths:
            return candidate
        if candidate in stem_to_path:
            return stem_to_path[candidate]
    for path in file_paths:
        if path.endswith(f"/{normalized}") or path.endswith(f"/{normalized}.py") or path.endswith(f"/{normalized}.js"):
            return path
    return None


def _build_file_edges(ctx: RepoContext) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    files = ctx.files
    file_paths = {item["path"] for item in files}
    stem_to_path = {
        item["path"].rsplit(".", 1)[0]: item["path"]
        for item in files
        if "." in item["path"]
    }
    imports_by_file = ctx.module_map.get("imports_by_file", {}) or {}
    path_to_dir = {item["path"]: str(Path(item["path"]).parent).replace("\\", "/") for item in files}
    fan_in = ctx.module_map.get("fan_in", {}) or {}

    adjacency: dict[str, set[str]] = defaultdict(set)
    edges_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_edge(source: str, target: str, edge_type: str, weight: float) -> None:
        if source == target:
            return
        key = (source, target, edge_type)
        edge = edges_by_key.get(key)
        if edge is None:
            edges_by_key[key] = {"source": source, "target": target, "type": edge_type, "weight": float(weight)}
        else:
            edge["weight"] = max(float(edge["weight"]), float(weight))
        adjacency[source].add(target)
        adjacency[target].add(source)

    for item in files:
        path = item["path"]
        imports = imports_by_file.get(path, []) or []
        for raw_target in imports:
            resolved = _resolve_import_target(str(raw_target), file_paths, stem_to_path)
            if resolved:
                add_edge(path, resolved, "import", 3.0 + min(float(fan_in.get(resolved, 0)), 5.0) * 0.1)

    dir_groups: dict[str, list[str]] = defaultdict(list)
    for path, directory in path_to_dir.items():
        dir_groups[directory].append(path)
    for directory, members in dir_groups.items():
        if directory in {".", ""} or len(members) < 2:
            continue
        ranked = sorted(members, key=lambda p: fan_in.get(p, 0), reverse=True)[:8]
        for i, src in enumerate(ranked):
            for dst in ranked[i + 1 :]:
                add_edge(src, dst, "colocated", 1.0)

    suffix_groups: dict[str, list[str]] = defaultdict(list)
    for item in files:
        suffix_groups[item["suffix"]].append(item["path"])
    for suffix, members in suffix_groups.items():
        if suffix not in {".py", ".js", ".ts", ".tsx", ".java", ".go", ".rs"}:
            continue
        top_members = sorted(members, key=lambda p: fan_in.get(p, 0), reverse=True)[:6]
        for i, src in enumerate(top_members):
            for dst in top_members[i + 1 :]:
                src_parts = src.split("/")[:-1]
                dst_parts = dst.split("/")[:-1]
                if src_parts and dst_parts and src_parts[0] == dst_parts[0]:
                    add_edge(src, dst, "package_boundary", 0.7)

    return list(edges_by_key.values()), adjacency


def _label_propagation(
    paths: list[str],
    adjacency: dict[str, set[str]],
    weighted_neighbors: dict[str, list[tuple[str, float]]],
    importance: dict[str, float],
    iterations: int = 8,
) -> dict[str, str]:
    labels = {path: path for path in paths}
    ranked = sorted(paths, key=lambda p: (-importance.get(p, 0.0), p))
    for _ in range(iterations):
        changed = False
        for path in ranked:
            votes: Counter[str] = Counter()
            for neighbor, weight in weighted_neighbors.get(path, []):
                votes[labels.get(neighbor, neighbor)] += weight
            if not votes:
                continue
            best_label, _ = sorted(votes.items(), key=lambda x: (-x[1], x[0]))[0]
            if labels[path] != best_label:
                labels[path] = best_label
                changed = True
        if not changed:
            break

    isolated = [path for path in paths if not adjacency.get(path)]
    for path in isolated:
        parts = path.split("/")
        labels[path] = parts[0] if len(parts) > 1 else "root"
    return labels


def _hierarchy_for_members(members: list[str]) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for path in sorted(members):
        parts = path.split("/")
        cur = tree
        for depth, part in enumerate(parts):
            if depth == len(parts) - 1:
                cur.setdefault("__files__", []).append(part)
            else:
                cur = cur.setdefault(part, {})
    return tree


def build_architecture_ir(ctx: RepoContext) -> dict[str, Any]:
    entrypoints = set(ctx.module_map.get("entrypoints", []) or [])
    build_files = set(ctx.module_map.get("build_files", []) or [])
    configs = set(ctx.module_map.get("configs", []) or [])
    docs = {item["path"] for item in ctx.docs_files}
    fan_in = ctx.module_map.get("fan_in", {}) or {}

    nodes: list[dict[str, Any]] = []
    for item in ctx.files:
        path = item["path"]
        importance = _node_importance(path, int(fan_in.get(path, 0)), entrypoints, build_files, configs, docs)
        node_kind = "source"
        if path in entrypoints:
            node_kind = "entrypoint"
        elif path in build_files:
            node_kind = "build"
        elif path in configs:
            node_kind = "config"
        elif path in docs:
            node_kind = "doc"
        nodes.append(
            {
                "id": _stable_id("file", path),
                "kind": node_kind,
                "path": path,
                "label": Path(path).name,
                "directory": str(Path(path).parent).replace("\\", "/"),
                "importance": importance,
                "fan_in": int(fan_in.get(path, 0)),
                "language": item.get("language", "text"),
            }
        )

    directory_nodes: dict[str, dict[str, Any]] = {}
    for item in ctx.files:
        parts = item["path"].split("/")
        if len(parts) == 1:
            directory = "root"
            directory_nodes.setdefault(
                directory,
                {"id": _stable_id("dir", directory), "kind": "directory", "path": directory, "label": directory, "importance": 1.0},
            )
            continue
        for depth in range(1, min(3, len(parts))):
            directory = "/".join(parts[:depth])
            directory_nodes.setdefault(
                directory,
                {
                    "id": _stable_id("dir", directory),
                    "kind": "directory",
                    "path": directory,
                    "label": parts[depth - 1],
                    "importance": 1.0,
                },
            )

    file_edges, adjacency = _build_file_edges(ctx)
    weighted_neighbors: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for edge in file_edges:
        weighted_neighbors[edge["source"]].append((edge["target"], float(edge["weight"])))
        weighted_neighbors[edge["target"]].append((edge["source"], float(edge["weight"])))

    importance = {node["path"]: float(node["importance"]) for node in nodes}
    labels = _label_propagation([node["path"] for node in nodes], adjacency, weighted_neighbors, importance)

    communities_by_label: dict[str, list[str]] = defaultdict(list)
    for path, label in labels.items():
        communities_by_label[label].append(path)

    community_records: list[dict[str, Any]] = []
    path_to_community: dict[str, str] = {}
    ranked_communities = sorted(
        communities_by_label.items(),
        key=lambda item: (-sum(importance.get(path, 0.0) for path in item[1]), min(item[1])),
    )
    for idx, (_, members) in enumerate(ranked_communities, start=1):
        members = sorted(members)
        top_paths = sorted(members, key=lambda path: (-importance.get(path, 0.0), path))[:6]
        common_prefix = _common_prefix(members)
        label = common_prefix or Path(top_paths[0]).parent.name or Path(top_paths[0]).name
        community_id = f"community_{idx:02d}"
        record = {
            "community_id": community_id,
            "label": label or "root",
            "member_count": len(members),
            "members": members,
            "top_paths": top_paths,
            "entrypoints": [path for path in top_paths if path in entrypoints],
            "hierarchy": _hierarchy_for_members(members),
            "importance": round(sum(importance.get(path, 0.0) for path in members), 4),
        }
        community_records.append(record)
        for path in members:
            path_to_community[path] = community_id

    cross_edges: dict[tuple[str, str], float] = defaultdict(float)
    for edge in file_edges:
        src_community = path_to_community.get(edge["source"])
        dst_community = path_to_community.get(edge["target"])
        if not src_community or not dst_community or src_community == dst_community:
            continue
        key = tuple(sorted([src_community, dst_community]))
        cross_edges[key] += float(edge["weight"])

    top_cross_community_interactions = [
        {
            "source_community": src,
            "target_community": dst,
            "weight": round(weight, 4),
        }
        for (src, dst), weight in sorted(cross_edges.items(), key=lambda item: (-item[1], item[0]))[:16]
    ]

    containment_edges: list[dict[str, Any]] = []
    for item in ctx.files:
        parts = item["path"].split("/")
        if len(parts) == 1:
            containment_edges.append({"source": "root", "target": item["path"], "type": "contains", "weight": 1.0})
            continue
        parent = "/".join(parts[:-1])
        if parent not in directory_nodes:
            parent = parts[0]
        containment_edges.append({"source": parent, "target": item["path"], "type": "contains", "weight": 1.0})
    for directory in directory_nodes:
        if directory == "root":
            continue
        parent = "/".join(directory.split("/")[:-1]) or "root"
        if parent in directory_nodes or parent == "root":
            containment_edges.append({"source": parent, "target": directory, "type": "contains", "weight": 1.0})

    nodes.extend(directory_nodes.values())
    top_nodes = sorted(nodes, key=lambda node: (-float(node.get("importance", 0.0)), node.get("path", "")))[:24]

    return {
        "repo_name": ctx.sample.repo_name,
        "commit_id": ctx.sample.commit_id,
        "node_count": len(nodes),
        "edge_count": len(file_edges) + len(containment_edges),
        "nodes": nodes,
        "edges": file_edges + containment_edges,
        "communities": community_records,
        "hierarchy": {
            "type": "community_tree",
            "children": [
                {
                    "community_id": community["community_id"],
                    "label": community["label"],
                    "member_count": community["member_count"],
                    "hierarchy": community["hierarchy"],
                }
                for community in community_records
            ],
        },
        "entrypoints": sorted(entrypoints),
        "configs": sorted(configs),
        "build_files": sorted(build_files),
        "docs_anchors": sorted(docs),
        "top_nodes": top_nodes,
        "top_cross_community_interactions": top_cross_community_interactions,
    }


def _common_prefix(paths: list[str]) -> str:
    if not paths:
        return ""
    split_paths = [path.split("/")[:-1] for path in paths]
    prefix: list[str] = []
    for parts in zip(*split_paths):
        if len(set(parts)) != 1:
            break
        prefix.append(parts[0])
    return "/".join(prefix)


def render_mermaid_from_ir(ir: dict[str, Any], max_communities: int = 8, max_cross_edges: int = 10) -> str:
    communities = sorted(
        ir.get("communities", []),
        key=lambda item: (-float(item.get("importance", 0.0)), item.get("community_id", "")),
    )[:max_communities]
    community_map = {item["community_id"]: item for item in communities}

    lines = ["flowchart TD"]
    for community in communities:
        cid = community["community_id"].replace("-", "_")
        label = community.get("label") or community["community_id"]
        top_path = ", ".join(community.get("top_paths", [])[:2])
        display = f"{label}\\n{top_path}" if top_path else label
        lines.append(f"  {cid}[{display}]")

    for edge in ir.get("top_cross_community_interactions", [])[:max_cross_edges]:
        src = edge.get("source_community", "").replace("-", "_")
        dst = edge.get("target_community", "").replace("-", "_")
        if src not in {c["community_id"].replace("-", "_") for c in communities}:
            continue
        if dst not in {c["community_id"].replace("-", "_") for c in communities}:
            continue
        weight = float(edge.get("weight", 0.0))
        label = f"{weight:.1f}"
        lines.append(f"  {src} -->|{label}| {dst}")

    if len(lines) == 1:
        lines.extend(["  A[Repository]", "  B[Subsystem]", "  A --> B"])
    return "\n".join(lines)


def compact_ir_for_prompt(ir: dict[str, Any], max_communities: int = 6) -> dict[str, Any]:
    communities = sorted(
        ir.get("communities", []),
        key=lambda item: (-float(item.get("importance", 0.0)), item.get("community_id", "")),
    )[:max_communities]
    return {
        "entrypoints": ir.get("entrypoints", []),
        "configs": ir.get("configs", [])[:12],
        "build_files": ir.get("build_files", [])[:12],
        "docs_anchors": ir.get("docs_anchors", [])[:12],
        "communities": [
            {
                "community_id": c.get("community_id"),
                "label": c.get("label"),
                "member_count": c.get("member_count"),
                "top_paths": c.get("top_paths", [])[:6],
                "entrypoints": c.get("entrypoints", []),
            }
            for c in communities
        ],
        "top_cross_community_interactions": ir.get("top_cross_community_interactions", [])[:10],
        "top_nodes": [
            {
                "path": node.get("path"),
                "kind": node.get("kind"),
                "importance": node.get("importance"),
            }
            for node in ir.get("top_nodes", [])[:16]
        ],
    }


def section_keywords(section_name: str) -> list[str]:
    name = normalize_text(section_name)
    mapping = {
        "overview": ["overview", "purpose", "system", "repo", "readme", "introduction", "entrypoint"],
        "architecture": ["architecture", "module", "subsystem", "component", "dependency", "graph", "interaction"],
        "data flow": ["flow", "request", "pipeline", "runtime", "execution", "handler", "service"],
        "configuration dependencies": ["config", "settings", "dependency", "build", "package", "requirements", "script"],
        "how to run": ["run", "usage", "script", "cli", "command", "install", "build", "start"],
        "extension points": ["plugin", "hook", "extension", "interface", "custom", "override", "register"],
    }
    for key, values in mapping.items():
        if key in name:
            return values
    return [name]
