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

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.vllm_mode: bool = bool(self.config.get("vllm_mode", False))
        # Validate and build vLLM env overrides eagerly so failures surface
        # before any tasks run, not partway through a long eval.
        self._vllm_env: dict[str, str] = self._build_vllm_env() if self.vllm_mode else {}

    @property
    def backend(self) -> str:
        return "vllm" if self.vllm_mode else "default"

    @property
    def vllm_model(self) -> str:
        return self._vllm_env.get("ANTHROPIC_MODEL", "")

    def _build_vllm_env(self) -> dict[str, str]:
        """Return the env overrides needed for vLLM mode.

        Reads CLAUDE_CODE_VLLM_* from the environment (dotenv is already loaded
        by Config before the adapter is constructed). Raises ValueError early
        if any required variable is absent so the user gets a clear error.
        """
        base_url = (self.config.get("vllm_base_url") or
                    os.environ.get("CLAUDE_CODE_VLLM_BASE_URL", "")).strip()
        auth_token = (self.config.get("vllm_auth_token") or
                      os.environ.get("CLAUDE_CODE_VLLM_AUTH_TOKEN", "")).strip()
        model = (self.config.get("vllm_model") or
                 os.environ.get("CLAUDE_CODE_VLLM_MODEL", "")).strip()

        missing = [
            name for name, val in [
                ("CLAUDE_CODE_VLLM_BASE_URL", base_url),
                ("CLAUDE_CODE_VLLM_AUTH_TOKEN", auth_token),
                ("CLAUDE_CODE_VLLM_MODEL", model),
            ] if not val
        ]
        if missing:
            raise ValueError(
                "--claude-code-vllm is enabled but required config is missing: "
                + ", ".join(missing)
                + ". Set these in .env or the environment before running."
            )

        return {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": auth_token,
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        }

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

        model = self.config.get("model")
        if model:
            cmd.extend(["--model", model])

        env = os.environ.copy()
        if self.config.get("proxy"):
            env["HTTPS_PROXY"] = self.config["proxy"]
        # Apply vLLM overrides on top of the copied env (never mutates os.environ).
        if self.vllm_mode:
            env.update(self._vllm_env)

        t_start = time.time()
        first_action = 0.0
        base_sha = self._capture_base_sha(repo_path)

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
            patch = self._extract_patch(repo_path, base_ref=base_sha)

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
