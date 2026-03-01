from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def setup_logging(output_dir: str | Path, run_name: str = "run") -> logging.Logger:
    out = Path(output_dir)
    log_dir = out / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"{run_name}_{ts}.log"

    logger = logging.getLogger("codewiki_hpc")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    logger.info("logging initialized")
    logger.info(f"log file: {log_path}")
    return logger
