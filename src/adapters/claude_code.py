"""Claude Code CLI adapter."""

from __future__ import annotations

import json
import os
import subprocess
import time

from src.adapters.base import AgentAdapter
from src.core.models import AgentResult, TaskStatus, TokenUsage, Timestamps


class ClaudeCodeAdapter(AgentAdapter):
    name = "claude-code"

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def run(
        self, problem_statement: str, repo_path: str, instance_id: str
    ) -> AgentResult:
        cmd = [
            "claude", "-p", problem_statement,
            "--output-format", "json",
            "--max-budget-usd", str(self.max_budget),
            "--allowedTools", "Bash,Read,Write,Edit",
            "--dangerously-skip-permissions",
        ]

        env = os.environ.copy()
        if self.config.get("proxy"):
            env["HTTPS_PROXY"] = self.config["proxy"]

        t_start = time.time()
        first_action = 0.0

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=self.timeout,
                text=True,
                env=env,
                cwd=repo_path,
            )
            t_end = time.time()

            if proc.returncode != 0:
                return AgentResult(
                    instance_id=instance_id,
                    agent_name=self.name,
                    status=TaskStatus.ERROR,
                    error_message=proc.stderr[:2000],
                    timestamps=Timestamps(task_start=t_start, task_end=t_end),
                    raw_output=proc.stdout[:5000],
                )

            # Parse JSON output
            output = self._parse_output(proc.stdout)
            patch = self._extract_patch(repo_path)

            # Extract usage info
            usage = output.get("usage", {})
            raw_input = usage.get("input_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_write = usage.get("cache_creation_input_tokens", 0)
            token_usage = TokenUsage(
                input_tokens=raw_input + cache_read + cache_write,
                output_tokens=usage.get("output_tokens", 0),
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
            )

            cost = output.get("total_cost_usd", 0.0)
            num_turns = output.get("num_turns", 0)

            # Estimate first action time from duration_ms if available
            duration_ms = output.get("duration_ms", 0)
            duration_api_ms = output.get("duration_api_ms", 0)
            if duration_api_ms and num_turns > 0:
                first_action = t_start + (duration_api_ms / 1000.0 / num_turns)
            else:
                first_action = t_start + (t_end - t_start) * 0.1

            return AgentResult(
                instance_id=instance_id,
                agent_name=self.name,
                patch=patch,
                status=TaskStatus.SUCCESS,
                token_usage=token_usage,
                timestamps=Timestamps(
                    task_start=t_start,
                    task_end=t_end,
                    first_action=first_action,
                ),
                total_cost_usd=cost,
                convergence_steps=num_turns,
                raw_output=proc.stdout[:10000],
                model_name=output.get("model", ""),
            )

        except subprocess.TimeoutExpired:
            t_end = time.time()
            return AgentResult(
                instance_id=instance_id,
                agent_name=self.name,
                status=TaskStatus.ERROR,
                error_message=f"Timeout after {self.timeout}s",
                timestamps=Timestamps(task_start=t_start, task_end=t_end),
            )

    def _parse_output(self, stdout: str) -> dict:
        """Parse Claude Code JSON output. May contain multiple JSON objects."""
        # Claude Code outputs one JSON object per line sometimes
        lines = stdout.strip().split("\n")
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return {}
