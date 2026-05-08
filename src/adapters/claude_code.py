"""Claude Code CLI adapter."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time

from src.adapters.base import AgentAdapter
from src.core.models import AgentResult, TaskStatus, TokenUsage, Timestamps

logger = logging.getLogger("coding-agent-eval")


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
            "--output-format", "stream-json",
            "--verbose",
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
        first_action_time: float = 0.0
        base_sha = self._capture_base_sha(repo_path)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=repo_path,
        )

        stdout_lines: list[str] = []
        tool_call_count = 0

        try:
            while True:
                elapsed = time.time() - t_start
                if elapsed > self.timeout:
                    proc.kill()
                    proc.wait()
                    t_end = time.time()
                    return AgentResult(
                        instance_id=instance_id,
                        agent_name=self.name,
                        status=TaskStatus.ERROR,
                        error_message=f"Timeout after {self.timeout}s (tool calls so far: {tool_call_count})",
                        timestamps=Timestamps(task_start=t_start, task_end=t_end),
                        raw_output="".join(stdout_lines)[:5000],
                    )

                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if not line:
                    continue

                stdout_lines.append(line)

                stripped = line.strip()
                if not stripped.startswith("{"):
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")

                # assistant turn — look for tool_use items in content
                if etype == "assistant":
                    content = event.get("message", {}).get("content", [])
                    for item in content:
                        if not isinstance(item, dict) or item.get("type") != "tool_use":
                            continue
                        tool_call_count += 1
                        tool_name = item.get("name", "unknown")
                        if first_action_time == 0.0:
                            first_action_time = time.time()
                        logger.info(
                            f"    [{elapsed:.0f}s] tool call #{tool_call_count}: {tool_name}"
                        )

        except Exception as e:
            proc.kill()
            proc.wait()
            t_end = time.time()
            return AgentResult(
                instance_id=instance_id,
                agent_name=self.name,
                status=TaskStatus.ERROR,
                error_message=str(e),
                timestamps=Timestamps(task_start=t_start, task_end=t_end),
            )

        t_end = time.time()
        stdout = "".join(stdout_lines)
        stderr = proc.stderr.read() if proc.stderr else ""

        if proc.returncode != 0:
            return AgentResult(
                instance_id=instance_id,
                agent_name=self.name,
                status=TaskStatus.ERROR,
                error_message=stderr[:2000],
                timestamps=Timestamps(task_start=t_start, task_end=t_end),
                raw_output=stdout[:5000],
            )

        # _parse_output scans in reverse for the last JSON object, which is the
        # stream-json "result" event containing total_cost_usd / usage / num_turns.
        output = self._parse_output(stdout)
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
        first_action = first_action_time or (t_start + (t_end - t_start) * 0.1)

        logger.info(f"    Total tool calls: {tool_call_count}, elapsed: {t_end - t_start:.0f}s")

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
            raw_output=stdout[:10000],
            model_name=output.get("model", ""),
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
