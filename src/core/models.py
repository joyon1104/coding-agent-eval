"""Data models for Coding Agent Eval."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Timestamps:
    task_start: float = 0.0
    task_end: float = 0.0
    first_action: float = 0.0

    @property
    def e2e_time(self) -> float:
        if self.task_end and self.task_start:
            return self.task_end - self.task_start
        return 0.0

    @property
    def time_to_first_action(self) -> float:
        if self.first_action and self.task_start:
            return self.first_action - self.task_start
        return 0.0


@dataclass
class AgentResult:
    instance_id: str
    agent_name: str
    patch: str = ""
    status: TaskStatus = TaskStatus.PENDING
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    timestamps: Timestamps = field(default_factory=Timestamps)
    total_cost_usd: float = 0.0
    convergence_steps: int = 0
    error_message: str = ""
    raw_output: str = ""
    model_name: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> AgentResult:
        d = d.copy()
        d["status"] = TaskStatus(d.get("status", "pending"))
        d["token_usage"] = TokenUsage(**d.get("token_usage", {}))
        d["timestamps"] = Timestamps(**d.get("timestamps", {}))
        return cls(**d)

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: Path) -> AgentResult:
        return cls.from_dict(json.loads(path.read_text()))


@dataclass
class EvalTask:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    patch: str = ""  # gold patch
    test_patch: str = ""
    difficulty: str = "medium"
    version: str = ""
    environment_setup_commit: str = ""
    FAIL_TO_PASS: list[str] = field(default_factory=list)
    PASS_TO_PASS: list[str] = field(default_factory=list)

    @classmethod
    def from_swebench(cls, item: dict) -> EvalTask:
        fail_to_pass = item.get("FAIL_TO_PASS", "[]")
        pass_to_pass = item.get("PASS_TO_PASS", "[]")
        if isinstance(fail_to_pass, str):
            fail_to_pass = json.loads(fail_to_pass)
        if isinstance(pass_to_pass, str):
            pass_to_pass = json.loads(pass_to_pass)

        return cls(
            instance_id=item["instance_id"],
            repo=item.get("repo", ""),
            base_commit=item.get("base_commit", ""),
            problem_statement=item.get("problem_statement", ""),
            hints_text=item.get("hints_text", ""),
            patch=item.get("patch", ""),
            test_patch=item.get("test_patch", ""),
            difficulty=item.get("difficulty", "medium"),
            version=item.get("version", ""),
            environment_setup_commit=item.get("environment_setup_commit", ""),
            FAIL_TO_PASS=fail_to_pass,
            PASS_TO_PASS=pass_to_pass,
        )
