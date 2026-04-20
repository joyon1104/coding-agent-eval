"""OpenCode CLI adapter."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time

from src.adapters.base import AgentAdapter
from src.core.models import AgentResult, TaskStatus, TokenUsage, Timestamps

logger = logging.getLogger("coding-agent-eval")


class OpenCodeAdapter(AgentAdapter):
    name = "opencode"

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                ["opencode", "--version"],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def run(
        self, problem_statement: str, repo_path: str, instance_id: str
    ) -> AgentResult:
        cmd = [
            "opencode", "run", problem_statement,
            "--format", "json",
            "--dir", repo_path,
            "--dangerously-skip-permissions",
        ]

        model = self.config.get("model")
        if model:
            cmd.extend(["--model", model])

        env = os.environ.copy()
        if self.config.get("proxy"):
            env["HTTPS_PROXY"] = self.config["proxy"]

        t_start = time.time()
        base_sha = self._capture_base_sha(repo_path)

        try:
            # Use Popen for real-time progress logging
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )

            stdout_lines = []
            step_count = 0
            last_log_time = t_start

            while True:
                # Check timeout
                elapsed = time.time() - t_start
                if elapsed > self.timeout:
                    proc.kill()
                    proc.wait()
                    t_end = time.time()
                    return AgentResult(
                        instance_id=instance_id,
                        agent_name=self.name,
                        status=TaskStatus.ERROR,
                        error_message=f"Timeout after {self.timeout}s (steps completed: {step_count})",
                        timestamps=Timestamps(task_start=t_start, task_end=t_end),
                        raw_output="".join(stdout_lines)[:50000],
                    )

                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if not line:
                    continue

                stdout_lines.append(line)

                # Log progress from JSON events
                line_stripped = line.strip()
                if line_stripped.startswith("{"):
                    try:
                        event = json.loads(line_stripped)
                        etype = event.get("type", "")
                        now = time.time()

                        if etype == "step_start":
                            step_count += 1
                        elif etype == "tool_use":
                            tool = event.get("part", {}).get("tool", "")
                            if now - last_log_time > 10:  # Log every 10s max
                                logger.info(f"    [{elapsed:.0f}s] step {step_count}: {tool}")
                                last_log_time = now
                        elif etype == "error":
                            err = event.get("error", {}).get("data", {}).get("message", "")
                            logger.warning(f"    [{elapsed:.0f}s] error: {err[:200]}")
                    except json.JSONDecodeError:
                        pass

            t_end = time.time()
            stdout = "".join(stdout_lines)
            stderr = proc.stderr.read() if proc.stderr else ""

            if proc.returncode != 0:
                error_msg = self._extract_error(stdout) or stderr[:2000]
                return AgentResult(
                    instance_id=instance_id,
                    agent_name=self.name,
                    status=TaskStatus.ERROR,
                    error_message=error_msg,
                    timestamps=Timestamps(task_start=t_start, task_end=t_end),
                    raw_output=stdout[:5000],
                )

            # Parse JSON events from output
            events = self._parse_events(stdout)

            # Check for API errors in events
            api_error = self._extract_error(stdout)
            if api_error:
                return AgentResult(
                    instance_id=instance_id,
                    agent_name=self.name,
                    status=TaskStatus.ERROR,
                    error_message=api_error,
                    timestamps=Timestamps(task_start=t_start, task_end=t_end),
                    raw_output=stdout[:5000],
                )

            patch = self._extract_patch(repo_path, base_ref=base_sha)

            # Extract usage info from events
            token_usage, cost, event_model = self._extract_usage(events)
            model_name = model or event_model
            num_turns = self._count_turns(events)

            # Estimate first action time
            first_action_ts = self._find_first_action_time(events)
            if first_action_ts and first_action_ts > t_start:
                first_action = first_action_ts / 1000.0  # ms to seconds epoch
            else:
                first_action = t_start + (t_end - t_start) * 0.1

            logger.info(f"    Total steps: {num_turns}, elapsed: {t_end - t_start:.0f}s")

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
                raw_output=stdout[:50000],
                model_name=model_name,
            )

        except Exception as e:
            t_end = time.time()
            return AgentResult(
                instance_id=instance_id,
                agent_name=self.name,
                status=TaskStatus.ERROR,
                error_message=str(e),
                timestamps=Timestamps(task_start=t_start, task_end=t_end),
            )

    def _parse_events(self, stdout: str) -> list[dict]:
        """Parse JSON events from OpenCode output (one JSON object per line)."""
        events = []
        for line in stdout.strip().split("\n"):
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def _extract_error(self, stdout: str) -> str:
        """Extract error message from OpenCode JSON events."""
        for event in self._parse_events(stdout):
            if event.get("type") == "error":
                error_data = event.get("error", {})
                data = error_data.get("data", {})
                return data.get("message", error_data.get("name", "Unknown error"))
        return ""

    def _extract_usage(self, events: list[dict]) -> tuple[TokenUsage, float, str]:
        """Extract token usage and cost from OpenCode events.

        OpenCode emits step-finish/step_done events with nested usage:
          {"type": "step_done", "part": {"tokens": {"input":..., "output":...}, "cost":...}}
        or in session export format:
          {"type": "step-finish", "tokens": {"input":..., "output":...}, "cost":...}
        """
        total_input = 0
        total_output = 0
        cache_read = 0
        total_cost = 0.0
        model_name = ""

        for event in events:
            etype = event.get("type", "")

            if etype in ("step_done", "step_finish", "step-finish"):
                # tokens/cost can be at top level or inside "part"
                source = event
                if "part" in event and isinstance(event["part"], dict):
                    part = event["part"]
                    if "tokens" in part:
                        source = part

                tokens = source.get("tokens", {})
                if isinstance(tokens, dict):
                    total_input += tokens.get("input", 0)
                    total_output += tokens.get("output", 0)
                    cache = tokens.get("cache", {})
                    if isinstance(cache, dict):
                        cache_read += cache.get("read", 0)

                cost = source.get("cost", 0.0)
                if isinstance(cost, (int, float)):
                    total_cost += cost

                if not model_name:
                    model_name = source.get("model", "")

        token_usage = TokenUsage(
            input_tokens=total_input,
            output_tokens=total_output,
            cache_read_tokens=cache_read,
        )

        return token_usage, total_cost, model_name

    def _count_turns(self, events: list[dict]) -> int:
        """Count the number of step_start events as turns."""
        return sum(1 for e in events if e.get("type") == "step_start")

    def _find_first_action_time(self, events: list[dict]) -> float | None:
        """Find timestamp of the first tool use event."""
        for event in events:
            if event.get("type") in ("tool_start", "step_start"):
                return event.get("timestamp", 0)
        return None
