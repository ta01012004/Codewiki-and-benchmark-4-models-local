from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .dataset import RepoSample


@dataclass
class RepoContext:
    sample: RepoSample
    repo_path: Path
    files: list[dict[str, Any]]
    docs_files: list[dict[str, Any]]
    module_map: dict[str, Any]


class RepoManager:
    def __init__(self, cache_dir: str | Path, cfg: dict[str, Any], logger: Any):
        self.cache_dir = Path(cache_dir)
        self.repo_cache = self.cache_dir / "repos"
        self.repo_cache.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg
        self.logger = logger

        repo_cfg = cfg.get("repo", {})
        self.max_file_size = int(repo_cfg.get("max_file_size_bytes", 512_000))
        self.max_files = int(repo_cfg.get("max_files", 20_000))
        self.max_docs_chars = int(repo_cfg.get("max_docs_chars", 40_000))

        self.skip_dirs = set(repo_cfg.get("skip_dirs", []))
        self.skip_ext = set(repo_cfg.get("skip_extensions", []))
        self.git_retries = int(repo_cfg.get("git_retries", 3))
        self.git_retry_backoff_sec = int(repo_cfg.get("git_retry_backoff_sec", 3))

    @staticmethod
    def _is_noise_path(path: str) -> bool:
        tokens = set(re.split(r"[/._-]+", path.lower()))
        return bool(
            tokens.intersection(
                {
                    "test",
                    "tests",
                    "spec",
                    "specs",
                    "fixture",
                    "fixtures",
                    "mock",
                    "mocks",
                    "example",
                    "examples",
                    "patch",
                    "patches",
                    "benchmark",
                    "benchmarks",
                }
            )
        )

    def _run_git(self, args: list[str], cwd: Path | None = None, timeout: int = 300) -> None:
        cmd = ["git", *args]
        self.logger.info(f"git command: {' '.join(cmd)} (cwd={str(cwd) if cwd else '.'})")
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            check=False,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()
            out = (proc.stdout or "").strip()
            snippet = err[-1200:] if err else out[-1200:]
            raise RuntimeError(f"git failed (code={proc.returncode}): {' '.join(cmd)} | detail: {snippet}")

    def _run_git_with_retry(
        self,
        args: list[str],
        cwd: Path | None = None,
        timeout: int = 300,
        retries: int | None = None,
    ) -> None:
        max_retries = self.git_retries if retries is None else retries
        last_err: Exception | None = None
        for attempt in range(max_retries):
            try:
                self._run_git(args, cwd=cwd, timeout=timeout)
                return
            except Exception as e:
                last_err = e
                if attempt == max_retries - 1:
                    break
                sleep_sec = self.git_retry_backoff_sec * (attempt + 1)
                self.logger.warning(
                    f"git retry {attempt + 1}/{max_retries - 1} after failure: {e}. sleeping {sleep_sec}s"
                )
                time.sleep(sleep_sec)
        assert last_err is not None
        raise last_err

    @staticmethod
    def _safe_repo_name(repo_name: str) -> str:
        return re.sub(r"[^a-zA-Z0-9._-]+", "_", repo_name)

    @staticmethod
    def _safe_repo_id(repo_name: str, commit_id: str) -> str:
        safe_name = RepoManager._safe_repo_name(repo_name)
        short_commit = commit_id[:10] if commit_id else "unknown"
        return f"{safe_name}_{short_commit}"

    def prepare_repo(self, sample: RepoSample, timeout_sec: int = 600) -> Path:
        if not sample.repo_url:
            raise ValueError(f"Missing repo URL for sample idx={sample.idx} repo={sample.repo_name}")
        if not sample.commit_id:
            raise ValueError(f"Missing commit id for sample idx={sample.idx} repo={sample.repo_name}")

        safe_repo_name = self._safe_repo_name(sample.repo_name)
        repo_id = self._safe_repo_id(sample.repo_name, sample.commit_id)
        repo_path = self.repo_cache / repo_id
        runtime_cfg = self.cfg.get("runtime", {})
        offline = bool(
            runtime_cfg.get("offline", False)
            or os.getenv("HF_HUB_OFFLINE", "0") == "1"
            or os.getenv("TRANSFORMERS_OFFLINE", "0") == "1"
        )

        if not repo_path.exists():
            if offline:
                candidates = sorted(p for p in self.repo_cache.glob(f"{safe_repo_name}_*") if (p / ".git").exists())
                if candidates:
                    repo_path = candidates[0]
                    self.logger.info(f"offline fallback: using cached repo {repo_path}")
                else:
                    raise RuntimeError(
                        f"Offline mode enabled and cached repo is missing: {repo_path}. "
                        "Run once with network to populate cache."
                    )
            if not offline:
                self.logger.info("cloning repository", extra={"repo": sample.repo_url, "dest": str(repo_path)})
                self._run_git_with_retry(
                    ["clone", "--filter=blob:none", sample.repo_url, str(repo_path)],
                    timeout=timeout_sec,
                )
        elif not (repo_path / ".git").exists():
            if offline:
                raise RuntimeError(f"Offline mode enabled and invalid cached repo at {repo_path}")
            self.logger.warning("invalid repo cache, recloning", extra={"path": str(repo_path)})
            shutil.rmtree(repo_path, ignore_errors=True)
            self._run_git_with_retry(
                ["clone", "--filter=blob:none", sample.repo_url, str(repo_path)],
                timeout=timeout_sec,
            )

        if not offline:
            try:
                self._run_git_with_retry(["fetch", "--all", "--tags"], cwd=repo_path, timeout=timeout_sec)
            except Exception as e:
                self.logger.warning(f"git fetch failed, continuing with local cache only: {e}")
        try:
            self._run_git_with_retry(["checkout", "--force", sample.commit_id], cwd=repo_path, timeout=timeout_sec)
        except Exception as e:
            if offline:
                raise RuntimeError(
                    f"Commit {sample.commit_id} not available in offline cache for repo {sample.repo_name}"
                ) from e
            raise
        return repo_path

    def _is_binary(self, path: Path) -> bool:
        try:
            with path.open("rb") as f:
                block = f.read(2048)
            return b"\x00" in block
        except Exception:
            return True

    def _language_from_suffix(self, suffix: str) -> str:
        table = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".java": "java",
            ".cs": "csharp",
            ".cpp": "cpp",
            ".cc": "cpp",
            ".c": "c",
            ".h": "c",
            ".hpp": "cpp",
            ".go": "go",
            ".rs": "rust",
            ".rb": "ruby",
            ".php": "php",
            ".md": "markdown",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".json": "json",
            ".toml": "toml",
        }
        return table.get(suffix.lower(), "text")

    def _collect_files(self, repo_path: Path) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        count = 0

        for root, dirs, names in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in self.skip_dirs and not d.startswith(".git")]
            for name in names:
                if count >= self.max_files:
                    self.logger.warning("max_files reached", extra={"max_files": self.max_files})
                    return files

                p = Path(root) / name
                rel = p.relative_to(repo_path).as_posix()
                suffix = p.suffix.lower()

                if suffix in self.skip_ext:
                    continue

                try:
                    size = p.stat().st_size
                except FileNotFoundError:
                    continue

                if size > self.max_file_size:
                    continue
                if self._is_binary(p):
                    continue

                files.append(
                    {
                        "path": rel,
                        "abs_path": str(p),
                        "size": int(size),
                        "suffix": suffix,
                        "language": self._language_from_suffix(suffix),
                    }
                )
                count += 1

        return files

    @staticmethod
    def _read_text(path: Path, max_chars: int = 20000) -> str:
        try:
            txt = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
        return txt[:max_chars]

    def _collect_docs(self, repo_path: Path, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []
        patterns = (
            "readme",
            "contributing",
            "architecture",
            "design",
            "docs/",
        )
        total_chars = 0

        for item in files:
            rel = item["path"].lower()
            if any(p in rel for p in patterns) and rel.endswith((".md", ".rst", ".txt", ".adoc")):
                text = self._read_text(Path(item["abs_path"]), max_chars=8000)
                total_chars += len(text)
                docs.append({"path": item["path"], "text": text})
                if total_chars >= self.max_docs_chars:
                    break

        return docs

    def _parse_import_targets(self, text: str, language: str) -> list[str]:
        targets: list[str] = []
        lines = text.splitlines()

        py_pat = re.compile(r"^\s*(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+))")
        js_pat = re.compile(r"(?:import\s+.*?from\s+['\"]([^'\"]+)['\"]|require\(['\"]([^'\"]+)['\"]\))")
        c_pat = re.compile(r"^\s*#include\s*[<\"]([^>\"]+)[>\"]")
        java_pat = re.compile(r"^\s*import\s+([\w\.]+)")

        for line in lines[:800]:
            if language == "python":
                m = py_pat.search(line)
                if m:
                    targets.append(m.group(1) or m.group(2) or "")
            elif language in {"javascript", "typescript"}:
                m = js_pat.search(line)
                if m:
                    targets.append(m.group(1) or m.group(2) or "")
            elif language in {"c", "cpp"}:
                m = c_pat.search(line)
                if m:
                    targets.append(m.group(1) or "")
            elif language == "java":
                m = java_pat.search(line)
                if m:
                    targets.append(m.group(1) or "")

        return [t for t in targets if t]

    def _build_tree(self, files: list[dict[str, Any]], max_depth: int = 4) -> dict[str, Any]:
        tree: dict[str, Any] = {}
        for item in files:
            parts = item["path"].split("/")
            cur = tree
            for depth, part in enumerate(parts):
                if depth >= max_depth:
                    break
                if depth == len(parts) - 1:
                    cur.setdefault("__files__", []).append(part)
                else:
                    cur = cur.setdefault(part, {})
        return tree

    def _detect_entrypoints(self, files: list[dict[str, Any]]) -> list[str]:
        candidates = {
            "main.py",
            "train.py",
            "app.py",
            "run.py",
            "server.py",
            "index.js",
            "index.ts",
            "__main__.py",
        }
        out: list[tuple[float, str]] = []
        for item in files:
            name = item["path"].split("/")[-1]
            if name not in candidates:
                continue
            path = item["path"]
            score = 1.0
            if not self._is_noise_path(path):
                score += 4.0
            lowered = path.lower()
            if lowered.startswith(("src/", "lib/", "app/", "server/", "cli/", "cmd/", "shell/")):
                score += 3.0
            if lowered.count("/") <= 2:
                score += 2.0
            if name in {"main.py", "__main__.py", "server.py"}:
                score += 1.5
            out.append((score, path))
        out.sort(key=lambda x: (-x[0], x[1]))
        return [path for _, path in out[:16]]

    def _detect_build_files(self, files: list[dict[str, Any]]) -> list[str]:
        markers = {
            "requirements.txt",
            "pyproject.toml",
            "setup.py",
            "package.json",
            "pom.xml",
            "build.gradle",
            "CMakeLists.txt",
            "*.csproj",
        }
        out: list[str] = []
        for item in files:
            name = Path(item["path"]).name
            if name in markers or name.endswith(".csproj"):
                out.append(item["path"])
        return out

    def _detect_configs(self, files: list[dict[str, Any]]) -> list[str]:
        out: list[str] = []
        for item in files:
            path = item["path"].lower()
            if path.endswith((".yaml", ".yml", ".json", ".toml", ".ini")) and (
                "config" in path or "configs" in path or "settings" in path
            ):
                out.append(item["path"])
        return out

    def _dependency_hints(self, repo_path: Path, files: list[dict[str, Any]]) -> tuple[dict[str, list[str]], dict[str, int]]:
        imports_by_file: dict[str, list[str]] = {}
        fan_in: dict[str, int] = {f["path"]: 0 for f in files}

        normalized_paths = [f["path"] for f in files]
        normalized_no_suffix = {p.rsplit(".", 1)[0]: p for p in normalized_paths if "." in p}

        for item in files:
            abs_path = Path(item["abs_path"])
            text = self._read_text(abs_path, max_chars=20000)
            targets = self._parse_import_targets(text, item["language"])
            imports_by_file[item["path"]] = targets

            for target in targets:
                target_key = target.replace(".", "/").lstrip("./")
                if target_key in normalized_no_suffix:
                    fan_in[normalized_no_suffix[target_key]] += 1
                else:
                    for p in normalized_paths:
                        if p.endswith(f"/{target_key}.py") or p.endswith(f"/{target_key}"):
                            fan_in[p] += 1

        return imports_by_file, fan_in

    def build_context(self, sample: RepoSample, repo_path: Path) -> RepoContext:
        files = self._collect_files(repo_path)
        docs = self._collect_docs(repo_path, files)
        imports_by_file, fan_in = self._dependency_hints(repo_path, files)

        module_map = {
            "tree": self._build_tree(files),
            "entrypoints": self._detect_entrypoints(files),
            "build_files": self._detect_build_files(files),
            "configs": self._detect_configs(files),
            "imports_by_file": imports_by_file,
            "fan_in": fan_in,
            "file_count": len(files),
            "docs_count": len(docs),
        }

        return RepoContext(
            sample=sample,
            repo_path=repo_path,
            files=files,
            docs_files=docs,
            module_map=module_map,
        )

    def dump_context_json(self, ctx: RepoContext, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "repo_name": ctx.sample.repo_name,
            "repo_url": ctx.sample.repo_url,
            "commit_id": ctx.sample.commit_id,
            "repo_path": str(ctx.repo_path),
            "files": ctx.files,
            "docs_files": ctx.docs_files,
            "module_map": ctx.module_map,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
