"""Base adapter interface for AI coding agents."""

from __future__ import annotations

import os
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path

from src.core.corp_env import CorpConfig, build_host_env
from src.core.models import AgentResult, TaskStatus, TokenUsage, Timestamps


class AgentAdapter(ABC):
    """Base class for agent adapters."""

    name: str = "base"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.max_turns = self.config.get("max_turns", 50)
        self.max_budget = self.config.get("max_budget", 5.0)
        self.timeout = self.config.get("timeout", 1800)
        # Corporate-network config (no-op when --corp absent). Adapters merge
        # this into the subprocess env via build_subprocess_env() so corp
        # variables propagate to pip/npm/git invoked by the agent.
        self.corp_config: CorpConfig | None = self.config.get("corp_config")

    def build_subprocess_env(self) -> dict[str, str]:
        """Return a copy of os.environ merged with corp variables (if any).

        When corp mode is off this is just ``os.environ.copy()`` — identical
        to the previous behavior. When on, proxy/CA/mirror vars are layered
        on top so they reach pip/npm/git invoked by the agent.
        """
        env = os.environ.copy()
        if self.corp_config is not None:
            env.update(build_host_env(self.corp_config))
        return env

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
        - Returns git's output as-is (no strip). Unified diff is format-sensitive —
          a blank context line is literally " \\n", and .strip() would eat the
          space, corrupting the last hunk's line counts. _write_patch_file
          downstream handles trailing-newline normalization.
        - `--binary` preserves binary-file changes properly (agents rarely touch
          binaries, but without this they'd silently drop).
        """
        try:
            subprocess.run(
                ["git", "add", "-N", "."],
                cwd=repo_path, capture_output=True, timeout=30,
            )
            cmd = ["git", "diff", "--binary"]
            if base_ref:
                cmd.append(base_ref)
            result = subprocess.run(
                cmd,
                cwd=repo_path, capture_output=True, text=True, timeout=30,
            )
            return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""
