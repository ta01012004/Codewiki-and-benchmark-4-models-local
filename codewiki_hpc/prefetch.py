from __future__ import annotations

import argparse
from pathlib import Path

from .config import apply_cli_overrides, load_config
from .dataset import load_codewikibench
from .repo_manager import RepoManager
from .utils.logging import setup_logging


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Prefetch CodeWikiBench repositories on gateway (networked node)")
    p.add_argument("--config", type=str, default="configs/default.yaml")
    p.add_argument("--split", type=str, default=None)
    p.add_argument("--max_repos", type=int, default=None)
    p.add_argument("--output_dir", type=str, default=None)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--end", type=int, default=None)
    return p.parse_args()


def main() -> None:
    args = parse_args()

    class _Args:
        backend = None
        split = args.split
        max_repos = args.max_repos
        output_dir = args.output_dir
        gpus = None
        resume = True
        seed = None
        deterministic = False
        offline = False
        model_root = None

    cfg = apply_cli_overrides(load_config(args.config), _Args)
    cfg.setdefault("runtime", {})["offline"] = False

    output_dir = Path(cfg.get("output_dir", "outputs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(output_dir, run_name="prefetch")

    split = cfg.get("dataset", {}).get("split", "train")
    max_repos = cfg.get("dataset", {}).get("max_repos")
    samples = load_codewikibench(split=split, max_repos=max_repos, verbose_warnings=True)

    start = max(0, int(args.start))
    end = len(samples) if args.end is None else min(len(samples), int(args.end))
    shard = samples[start:end]

    repo_manager = RepoManager(cache_dir=output_dir / "cache", cfg=cfg, logger=logger)
    clone_timeout = int(cfg.get("runtime", {}).get("clone_timeout_sec", 1200))

    ok = 0
    fail = 0
    for i, sample in enumerate(shard, start=start):
        try:
            repo_path = repo_manager.prepare_repo(sample, timeout_sec=clone_timeout)
            logger.info(f"[prefetch-ok] idx={i} repo={sample.repo_name} path={repo_path}")
            ok += 1
        except Exception as e:
            logger.error(f"[prefetch-fail] idx={i} repo={sample.repo_name} error={type(e).__name__}: {e}")
            fail += 1

    logger.info(f"Prefetch finished: ok={ok} fail={fail} total={len(shard)}")


if __name__ == "__main__":
    main()
