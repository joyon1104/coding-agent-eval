"""Base adapter interface for AI coding agents."""

from __future__ import annotations

import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path

from src.core.models import AgentResult, TaskStatus, TokenUsage, Timestamps


class AgentAdapter(ABC):
    """Base class for agent adapters."""

    name: str = "base"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.max_turns = self.config.get("max_turns", 50)
        self.max_budget = self.config.get("max_budget", 5.0)
        self.timeout = self.config.get("timeout", 1800)

    @abstractmethod
    def run(
        self, problem_statement: str, repo_path: str, instance_id: str
    ) -> AgentResult:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...

    def _capture_base_sha(self, repo_path: str) -> str:
        """Snapshot HEAD before the agent runs, so _extract_patch can diff against it.

        This matters when an agent (e.g. OpenCode) auto-commits step snapshots,
        which moves HEAD and makes a plain `git diff` empty.
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path, capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return ""

    def _extract_patch(self, repo_path: str, base_ref: str = "") -> str:
        """Extract git diff (base_ref..worktree) from the repo after agent runs.

        - `git add -N .` registers untracked files as intent-to-add so diff sees them.
        - Comparing against the captured base_ref covers agents that commit during the
          session; falls back to plain `git diff` (vs HEAD) if no ref was captured.
        """
        try:
            subprocess.run(
                ["git", "add", "-N", "."],
                cwd=repo_path, capture_output=True, timeout=30,
            )
            cmd = ["git", "diff", base_ref] if base_ref else ["git", "diff"]
            result = subprocess.run(
                cmd,
                cwd=repo_path, capture_output=True, text=True, timeout=30,
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""
