"""Orchestrator: runs agents on tasks with resume support."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from src.adapters.base import AgentAdapter
from src.core.config import Config, PROJECT_ROOT
from src.core.models import AgentResult, EvalTask, TaskStatus
from src.evaluator.failure_classifier import (
    STAGE_REPO_CLONE,
    STAGE_SANDBOX_SETUP,
    STAGE_AGENT_EXECUTION,
    classify_agent_failure,
    classify_sandbox_failure,
)
from src.runner.logger import setup_logging, save_run_metadata
from src.runner.sandbox import DiskAwareSandbox, DiskSpaceError

console = Console()
logger = logging.getLogger("coding-agent-eval")


class Orchestrator:
    """Runs evaluation tasks with resume support."""

    def __init__(
        self,
        config: Config,
        run_id: str,
        model: str | None = None,
        extra_metadata: dict | None = None,
    ):
        self.config = config
        self.run_id = run_id
        self.model = model
        self.extra_metadata: dict = extra_metadata or {}
        self.results_dir = PROJECT_ROOT / "results" / "runs" / run_id
        self.sandbox = DiskAwareSandbox(config)

    def _result_path(self, instance_id: str) -> Path:
        return self.results_dir / "patches" / f"{instance_id}.json"

    def _is_completed(self, instance_id: str) -> bool:
        """Check if a task completed successfully. Error results are retried."""
        result_file = self._find_result_file(instance_id)
        if not result_file:
            return False
        try:
            result = AgentResult.load(result_file)
            return result.status == TaskStatus.SUCCESS
        except Exception:
            return False

    def _find_result_file(self, instance_id: str) -> Path | None:
        # Check new layout first, then legacy
        new_path = self.results_dir / "patches" / f"{instance_id}.json"
        if new_path.exists():
            return new_path
        for d in self.results_dir.iterdir():
            if d.is_dir() and d.name not in ("patches", "eval", "reports"):
                legacy = d / f"{instance_id}.json"
                if legacy.exists():
                    return legacy
        return None

    def _load_completed(self, instance_id: str) -> AgentResult:
        result_file = self._find_result_file(instance_id)
        if result_file:
            return AgentResult.load(result_file)
        raise FileNotFoundError(f"Result not found: {instance_id}")

    def run(
        self,
        tasks: list[EvalTask],
        agents: list[AgentAdapter],
    ) -> dict[str, list[AgentResult]]:
        """Run all tasks for all agents. Skips already completed tasks."""
        setup_logging(self.run_id)

        # Clean up stale resources from previous runs.
        # NOTE: Only our own /tmp/cae_* workdirs — never touch Docker resources
        # automatically, since this runs on shared servers where global
        # `docker prune` would wipe other developers' stopped containers and
        # dangling build layers.
        self.sandbox.cleanup_stale_workdirs()

        agent = agents[0]  # New layout: one agent per run

        # Capture start time once and reuse for the completion record so that
        # `completed_at - started_at` is a real duration, not ~0 from being
        # re-stamped at end-of-run.
        started_at = datetime.now().isoformat()

        save_run_metadata(self.run_id, {
            "run_id": self.run_id,
            "agent": agent.name,
            "model": self.model or "",
            "tier": self.config.tier,
            "started_at": started_at,
            "num_tasks": len(tasks),
            "agents": [a.name for a in agents],
            "environment": self.config.env_info.summary(),
            **self.extra_metadata,
        })

        all_results: dict[str, list[AgentResult]] = {}

        for agent in agents:
            agent_results = self._run_agent(agent, tasks)
            all_results[agent.name] = agent_results

        # Update metadata with completion. Reuse captured `started_at` so the
        # delta to completed_at reflects the actual Step 1 wall-clock time.
        save_run_metadata(self.run_id, {
            "run_id": self.run_id,
            "agent": agents[0].name,
            "model": self.model or "",
            "tier": self.config.tier,
            "started_at": started_at,
            "completed_at": datetime.now().isoformat(),
            "num_tasks": len(tasks),
            "agents": [a.name for a in agents],
            "environment": self.config.env_info.summary(),
            "results_summary": {
                agent: {
                    "total": len(results),
                    "success": sum(1 for r in results if r.status == TaskStatus.SUCCESS),
                    "error": sum(1 for r in results if r.status == TaskStatus.ERROR),
                }
                for agent, results in all_results.items()
            },
            **self.extra_metadata,
        })

        return all_results

    def _run_agent(
        self, agent: AgentAdapter, tasks: list[EvalTask]
    ) -> list[AgentResult]:
        """Run all tasks for a single agent."""
        results: list[AgentResult] = []
        skipped = 0
        total = len(tasks)

        logger.info(f"\n{'='*60}")
        logger.info(f"Agent: {agent.name} | Tasks: {total}")
        logger.info(f"{'='*60}")

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            ptask = progress.add_task(
                f"{agent.name}", total=total
            )

            for i, task in enumerate(tasks):
                # Check if already completed (resume support)
                if self._is_completed(task.instance_id):
                    result = self._load_completed(task.instance_id)
                    results.append(result)
                    skipped += 1
                    progress.update(ptask, advance=1)
                    continue

                progress.update(
                    ptask,
                    description=f"{agent.name} [{i+1}/{total}] {task.instance_id}",
                )

                result = self._run_single(agent, task)
                results.append(result)

                # Save immediately
                result.save(self._result_path(task.instance_id))

                progress.update(ptask, advance=1)

        logger.info(
            f"  Done: {total - skipped} run, {skipped} skipped"
        )
        return results

    def _run_single(self, agent: AgentAdapter, task: EvalTask) -> AgentResult:
        """Run a single task with a single agent."""
        logger.info(f"  Running: {task.instance_id}")

        # Check disk
        try:
            self.sandbox.check_disk()
        except DiskSpaceError as e:
            err_text = str(e)
            logger.error(f"  Disk error: {err_text}")
            cat, root = classify_sandbox_failure(err_text)
            return AgentResult(
                instance_id=task.instance_id,
                agent_name=agent.name,
                status=TaskStatus.ERROR,
                error_message=err_text,
                failure_stage=STAGE_SANDBOX_SETUP,
                failure_category=cat,
                root_cause=root,
            )

        # Setup repo
        try:
            repo_path = self.sandbox.setup_repo(
                task.instance_id, task.repo, task.base_commit
            )
        except Exception as e:
            err_text = f"Repo setup failed: {e}"
            logger.error(f"  {err_text}")
            cat, root = classify_sandbox_failure(str(e))
            return AgentResult(
                instance_id=task.instance_id,
                agent_name=agent.name,
                status=TaskStatus.ERROR,
                error_message=err_text,
                failure_stage=STAGE_REPO_CLONE,
                failure_category=cat,
                root_cause=root,
            )

        # Run agent
        try:
            result = agent.run(task.problem_statement, repo_path, task.instance_id)
            logger.info(
                f"  Completed: {result.status.value} | "
                f"Cost: ${result.total_cost_usd:.3f} | "
                f"Time: {result.timestamps.e2e_time:.1f}s"
            )
        except Exception as e:
            err_text = str(e)
            logger.error(f"  Agent error: {err_text}")
            cat, root = classify_agent_failure(err_text)
            result = AgentResult(
                instance_id=task.instance_id,
                agent_name=agent.name,
                status=TaskStatus.ERROR,
                error_message=err_text,
                failure_stage=STAGE_AGENT_EXECUTION,
                failure_category=cat,
                root_cause=root,
            )
        finally:
            if self.sandbox.clean_after:
                self.sandbox.cleanup(task.instance_id)

        return result
