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

    def _extract_patch(self, repo_path: str) -> str:
        """Extract git diff from the repo after agent runs."""
        try:
            result = subprocess.run(
                ["git", "diff"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""
