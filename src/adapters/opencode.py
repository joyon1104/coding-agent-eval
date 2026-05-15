"""OpenCode CLI adapter."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time

from src.adapters.base import AgentAdapter
from src.core.models import AgentResult, TaskStatus, TokenUsage, Timestamps
from src.evaluator.failure_classifier import (
    CAT_INTERNAL_ERROR,
    CAT_QUOTA_EXCEEDED,
    CAT_TIMEOUT,
    STAGE_AGENT_EXECUTION,
)

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

    def _build_cmd(self, problem_statement: str, repo_path: str) -> list[str]:
        """Build the CLI command list for this adapter.

        Subclasses override this to change the invocation (e.g. prepend ulw
        for oh-my-opencode) without duplicating the run() loop.
        --pure disables external plugins (including oh-my-opencode) so that
        this adapter always runs vanilla OpenCode regardless of what is installed.
        """
        cmd = [
            "opencode", "run", "--pure", problem_statement,
            "--format", "json",
            "--dir", repo_path,
            "--dangerously-skip-permissions",
        ]
        model = self.config.get("model")
        if model:
            cmd.extend(["--model", model])
        return cmd

    def run(
        self, problem_statement: str, repo_path: str, instance_id: str
    ) -> AgentResult:
        cmd = self._build_cmd(problem_statement, repo_path)

        # Starts from os.environ + corp-mode overrides (proxy/CA/mirrors).
        env = self.build_subprocess_env()
        if self.config.get("proxy"):
            env["HTTPS_PROXY"] = self.config["proxy"]

        # Sanitize the cmd for logging: the problem_statement (GitHub issue
        # body) can be hundreds of lines including tracebacks that look like
        # real errors. Replace it with a short metadata placeholder. Full text
        # is preserved in patches/<instance_id>.json.
        placeholder = f"<task:{instance_id}, len={len(problem_statement)} chars>"
        sanitized_cmd = [
            arg.replace(problem_statement, placeholder) if problem_statement in arg else arg
            for arg in cmd
        ]
        logger.info(f"  CMD: {' '.join(sanitized_cmd)}")
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

            while True:
                # Check timeout
                elapsed = time.time() - t_start
                if elapsed > self.timeout:
                    proc.kill()
                    proc.wait()
                    t_end = time.time()
                    logger.error(
                        f"  Timeout after {self.timeout}s "
                        f"(steps completed: {step_count})"
                    )
                    return AgentResult(
                        instance_id=instance_id,
                        agent_name=self.name,
                        status=TaskStatus.ERROR,
                        error_message=f"Timeout after {self.timeout}s (steps completed: {step_count})",
                        timestamps=Timestamps(task_start=t_start, task_end=t_end),
                        raw_output="".join(stdout_lines)[:50000],
                        failure_stage=STAGE_AGENT_EXECUTION,
                        failure_category=CAT_TIMEOUT,
                        root_cause="agent_execution_timeout",
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

                        if etype == "step_start":
                            step_count += 1
                            logger.info(f"    [{elapsed:.0f}s] step {step_count} started")
                        elif etype == "tool_use":
                            tool = event.get("part", {}).get("tool", "unknown")
                            tool_input = event.get("part", {}).get("input", {})
                            detail = ""
                            if isinstance(tool_input, dict):
                                detail = tool_input.get("command") or tool_input.get("path") or ""
                                if detail:
                                    detail = f" ({str(detail)[:80]})"
                            logger.info(
                                f"    [{elapsed:.0f}s] step {step_count}: "
                                f"tool_use → {tool}{detail}"
                            )
                        elif etype in ("step_done", "step_finish", "step-finish"):
                            source = event
                            if "part" in event and isinstance(event["part"], dict):
                                part = event["part"]
                                if "tokens" in part:
                                    source = part
                            tokens = source.get("tokens", {})
                            tok_in = tokens.get("input", 0) if isinstance(tokens, dict) else 0
                            tok_out = tokens.get("output", 0) if isinstance(tokens, dict) else 0
                            cost_step = source.get("cost", 0.0)
                            logger.info(
                                f"    [{elapsed:.0f}s] step {step_count} done | "
                                f"tokens in={tok_in} out={tok_out} | "
                                f"cost=${cost_step:.4f}"
                            )
                        elif etype == "error":
                            # Log raw JSON first so nothing is lost, then
                            # extract the human-readable message separately.
                            logger.error(
                                f"    [{elapsed:.0f}s] ERROR event (raw): "
                                f"{line_stripped}"
                            )
                            err_data = event.get("error", {})
                            err_msg = (
                                err_data.get("data", {}).get("message")
                                or err_data.get("message")
                                or err_data.get("name")
                                or str(err_data)
                            )
                            logger.error(
                                f"    [{elapsed:.0f}s] ERROR event (message): "
                                f"{err_msg}"
                            )
                    except json.JSONDecodeError:
                        pass

            t_end = time.time()
            stdout = "".join(stdout_lines)
            stderr = proc.stderr.read() if proc.stderr else ""

            logger.info(
                f"  Process exited: code={proc.returncode} | "
                f"elapsed={t_end - t_start:.1f}s | steps={step_count}"
            )

            if proc.returncode != 0:
                error_msg = self._extract_error(stdout) or stderr or f"exit code {proc.returncode}"
                logger.error(f"  Non-zero exit (code={proc.returncode}): {error_msg}")
                if stderr.strip():
                    logger.error(f"  STDERR (full):\n{stderr}")
                if stdout_lines:
                    logger.error(
                        f"  STDOUT (full, {len(stdout_lines)} lines):\n"
                        + "".join(stdout_lines)
                    )
                cat, root = self._classify_opencode_error(error_msg)
                return AgentResult(
                    instance_id=instance_id,
                    agent_name=self.name,
                    status=TaskStatus.ERROR,
                    error_message=error_msg,
                    timestamps=Timestamps(task_start=t_start, task_end=t_end),
                    raw_output=stdout[:50000],
                    failure_stage=STAGE_AGENT_EXECUTION,
                    failure_category=cat,
                    root_cause=root,
                    failure_details={"exit_code": proc.returncode, "stderr": stderr},
                )

            # Parse JSON events from output
            events = self._parse_events(stdout)

            # Check for API errors in events — log raw lines that contained
            # errors so the original payload is preserved in run.log.
            api_error = self._extract_error(stdout)
            if api_error:
                logger.error(f"  API error in event stream: {api_error}")
                error_raw_lines = [
                    l.strip() for l in stdout_lines
                    if l.strip().startswith("{")
                    and '"type":"error"' in l.replace(" ", "")
                ]
                if error_raw_lines:
                    logger.error(
                        "  Error event(s) raw JSON:\n"
                        + "\n".join(error_raw_lines)
                    )
                if stderr.strip():
                    logger.error(f"  STDERR (full):\n{stderr}")
                cat, root = self._classify_opencode_error(api_error)
                return AgentResult(
                    instance_id=instance_id,
                    agent_name=self.name,
                    status=TaskStatus.ERROR,
                    error_message=api_error,
                    timestamps=Timestamps(task_start=t_start, task_end=t_end),
                    raw_output=stdout[:50000],
                    failure_stage=STAGE_AGENT_EXECUTION,
                    failure_category=cat,
                    root_cause=root,
                    failure_details={"source": "event_stream_error", "stderr": stderr},
                )

            patch = self._extract_patch(repo_path, base_ref=base_sha)
            if not patch:
                logger.warning("  Patch is empty — agent made no file changes")
            else:
                logger.info(f"  Patch extracted: {patch.count(chr(10))} lines")

            # Extract usage info from events
            token_usage, cost, event_model = self._extract_usage(events)
            model_name = self.config.get("model") or event_model
            num_turns = self._count_turns(events)

            # Estimate first action time
            first_action_ts = self._find_first_action_time(events)
            if first_action_ts and first_action_ts > t_start:
                first_action = first_action_ts / 1000.0  # ms to seconds epoch
            else:
                first_action = t_start + (t_end - t_start) * 0.1

            logger.info(
                f"  Done: turns={num_turns} | "
                f"tokens={token_usage.total_tokens} | "
                f"cost=${cost:.4f} | model={model_name}"
            )

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
                failure_stage=STAGE_AGENT_EXECUTION,
                failure_category=CAT_INTERNAL_ERROR,
                root_cause="subprocess_exception",
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

    def _classify_opencode_error(self, error_msg: str) -> tuple[str, str]:
        """Classify an OpenCode error message into (failure_category, root_cause).

        Errors from upstream providers (Anthropic / Google / OpenAI) usually
        surface as plain text in OpenCode's JSON ``error`` events.
        """
        low = (error_msg or "").lower()
        if any(x in low for x in ("rate limit", "rate_limit", "quota", "429", "you've hit your limit")):
            return CAT_QUOTA_EXCEEDED, "rate_limit_exceeded"
        if "overloaded" in low or "529" in low:
            return CAT_TIMEOUT, "api_overloaded"
        if "timeout" in low or "timed out" in low:
            return CAT_TIMEOUT, "api_timeout"
        if "auth" in low or "unauthorized" in low or "401" in low or "403" in low:
            return "configuration_error", "api_auth_failed"
        return CAT_INTERNAL_ERROR, "api_error"

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
