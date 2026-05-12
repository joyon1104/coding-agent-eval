"""Run logging and trajectory tracking."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.text import Text

from src.core.config import PROJECT_ROOT


def setup_logging(run_id: str, level: int = logging.INFO) -> Path:
    """Configure the 'coding-agent-eval' logger to write to results/runs/<run_id>/run.log.

    Idempotent — calling twice for the same run_id is a no-op so multiple
    entry points (run_eval, run_docker_eval, generate_report, orchestrator)
    can all call this safely. Returns the run.log path so callers can wire
    it to a LoggingConsole instance.
    """
    log_dir = PROJECT_ROOT / "results" / "runs" / run_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "run.log"

    logger = logging.getLogger("coding-agent-eval")

    # Fast path — handler for this exact log file is already attached.
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler):
            try:
                if Path(h.baseFilename).resolve() == log_file.resolve():
                    return log_file
            except OSError:
                continue

    logger.setLevel(level)
    logger.handlers.clear()

    is_rerun = log_file.exists()
    fh = logging.FileHandler(log_file, mode="a")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    ))
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    if is_rerun:
        logger.info(f"\n{'='*60}")
        logger.info(f"Re-run started at {datetime.now().isoformat()}")
        logger.info(f"{'='*60}")

    return log_file


class LoggingConsole(Console):
    """rich.Console subclass that mirrors every print() to a log file.

    Terminal output is unchanged (markup, colors, wrapping). The log file
    receives the same text with rich markup stripped, prefixed with the
    standard 'YYYY-MM-DD HH:MM:SS,mmm [INFO] ' header so it interleaves
    cleanly with messages emitted by the 'coding-agent-eval' logger.

    Set the log path via the constructor or set_log_path() once the run_id
    is known. Before a path is set, print() behaves like a normal Console.
    """

    def __init__(self, log_path: Path | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._log_path: Path | None = log_path

    def set_log_path(self, path: Path | None) -> None:
        self._log_path = path

    def print(self, *objects, sep: str = " ", end: str = "\n", **kwargs) -> None:
        super().print(*objects, sep=sep, end=end, **kwargs)
        if not self._log_path or not objects:
            return
        try:
            parts: list[str] = []
            for obj in objects:
                if isinstance(obj, str):
                    parts.append(Text.from_markup(obj).plain)
                else:
                    parts.append(str(obj))
            line = sep.join(parts).rstrip("\n")
            if not line.strip():
                return
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
            with open(self._log_path, "a", encoding="utf-8") as f:
                first, *rest = line.split("\n")
                f.write(f"{ts} [INFO] {first}\n")
                for extra in rest:
                    f.write(f"{extra}\n")
        except Exception:
            # Never let logging break the main flow.
            pass


def save_run_metadata(run_id: str, metadata: dict):
    path = PROJECT_ROOT / "results" / "runs" / run_id / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False, default=str))
