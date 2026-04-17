"""Run logging and trajectory tracking."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from src.core.config import PROJECT_ROOT


def setup_logging(run_id: str, level: int = logging.INFO) -> logging.Logger:
    log_dir = PROJECT_ROOT / "results" / "runs" / run_id
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("coding-agent-eval")
    logger.setLevel(level)
    logger.handlers.clear()

    # File handler (append mode — re-runs add to existing log)
    log_file = log_dir / "run.log"
    is_rerun = log_file.exists()
    fh = logging.FileHandler(log_file, mode="a")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    ))
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    if is_rerun:
        logger.info(f"\n{'='*60}")
        logger.info(f"Re-run started at {datetime.now().isoformat()}")
        logger.info(f"{'='*60}")

    return logger


def save_run_metadata(run_id: str, metadata: dict):
    path = PROJECT_ROOT / "results" / "runs" / run_id / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False, default=str))
