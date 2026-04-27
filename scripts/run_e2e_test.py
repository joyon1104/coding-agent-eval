#!/usr/bin/env python3
"""
End-to-end pipeline test using mock adapter.
Tests the full flow: data load -> agent run -> metrics -> report
without requiring actual AI agents or Docker.
"""

import sys
import os
import json
import time
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from rich.console import Console

from src.core.config import Config, PROJECT_ROOT
from src.core.models import AgentResult, EvalTask, TaskStatus, TokenUsage, Timestamps
from src.dataset.loader import load_dataset_for_tier
from src.dataset.sampler import sample_tasks
from src.evaluator.swebench_harness import EvalResult
from src.metrics.accuracy import task_resolution_rate
from src.metrics.cost import avg_tokens_per_task, avg_cost_per_task, cost_per_resolved_task, token_efficiency
from src.metrics.latency import avg_e2e_time, avg_time_to_first_action
from src.metrics.process import avg_convergence_steps
from src.reporter.scorer import score_agent
from src.reporter.formatter import format_markdown, format_json, save_report
from src.runner.logger import save_run_metadata

console = Console()


class MockAgentAdapter:
    """Mock adapter that generates synthetic results for testing."""
    name = "mock-claude-code"

    def __init__(self, resolve_rate: float = 0.6):
        self.resolve_rate = resolve_rate
        self.rng = random.Random(42)

    def run(self, task: EvalTask) -> AgentResult:
        # Simulate execution time
        e2e_time = self.rng.uniform(30, 300)
        ttfa = self.rng.uniform(2, 10)
        t_start = time.time()

        # Simulate success/failure
        resolves = self.rng.random() < self.resolve_rate
        patch = "diff --git a/test.py b/test.py\n--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-old\n+new" if resolves else ""

        input_tokens = self.rng.randint(30000, 200000)
        output_tokens = self.rng.randint(5000, 50000)

        # Cost estimate ($3/M input, $15/M output for Sonnet)
        cost = (input_tokens * 3.0 / 1_000_000) + (output_tokens * 15.0 / 1_000_000)

        return AgentResult(
            instance_id=task.instance_id,
            agent_name=self.name,
            patch=patch,
            status=TaskStatus.SUCCESS,
            token_usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=self.rng.randint(0, 50000),
            ),
            timestamps=Timestamps(
                task_start=t_start,
                task_end=t_start + e2e_time,
                first_action=t_start + ttfa,
            ),
            total_cost_usd=cost,
            convergence_steps=self.rng.randint(3, 40),
            model_name="claude-sonnet-4-20250514",
        )


class MockAgentAdapter2:
    """Second mock adapter for comparison."""
    name = "mock-codex"

    def __init__(self, resolve_rate: float = 0.4):
        self.resolve_rate = resolve_rate
        self.rng = random.Random(99)

    def run(self, task: EvalTask) -> AgentResult:
        e2e_time = self.rng.uniform(20, 200)
        ttfa = self.rng.uniform(1, 5)
        t_start = time.time()

        resolves = self.rng.random() < self.resolve_rate
        patch = "diff --git a/fix.py ...\n-bug\n+fix" if resolves else ""

        input_tokens = self.rng.randint(20000, 150000)
        output_tokens = self.rng.randint(3000, 30000)
        cost = (input_tokens * 2.0 / 1_000_000) + (output_tokens * 8.0 / 1_000_000)

        return AgentResult(
            instance_id=task.instance_id,
            agent_name=self.name,
            patch=patch,
            status=TaskStatus.SUCCESS,
            token_usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            timestamps=Timestamps(
                task_start=t_start,
                task_end=t_start + e2e_time,
                first_action=t_start + ttfa,
            ),
            total_cost_usd=cost,
            convergence_steps=self.rng.randint(5, 50),
            model_name="o3",
        )


def main():
    console.print("[bold blue]Coding Agent Eval — E2E Pipeline Test[/bold blue]\n")

    run_id = "e2e-test"

    # Step 1: Environment detection
    console.print("[bold]Step 1: Environment Detection[/bold]")
    config = Config(tier="local", offline=True)
    console.print(f"  {config.env_info.summary()}")
    console.print(f"  Tier: {config.tier}")

    # Step 2: Load dataset
    console.print("\n[bold]Step 2: Load Dataset[/bold]")
    try:
        tasks = load_dataset_for_tier(config)
        console.print(f"  Loaded: {len(tasks)} instances")
    except FileNotFoundError:
        console.print("  [yellow]Dataset not found, creating test data...[/yellow]")
        os.system(f"python3 {PROJECT_ROOT / 'scripts' / 'create_test_data.py'}")
        tasks = load_dataset_for_tier(config)
        console.print(f"  Loaded: {len(tasks)} instances")

    sampled = sample_tasks(tasks, "local", sample_size=5)
    console.print(f"  Sampled: {len(sampled)} instances")
    for t in sampled:
        console.print(f"    - {t.instance_id} ({t.difficulty})")

    # Step 3: Run mock agents
    console.print("\n[bold]Step 3: Run Agents (Mock)[/bold]")
    agents = [MockAgentAdapter(resolve_rate=0.6), MockAgentAdapter2(resolve_rate=0.4)]

    all_results: dict[str, list[AgentResult]] = {}
    all_eval_results: dict[str, list[EvalResult]] = {}

    results_dir = PROJECT_ROOT / "results" / "runs" / run_id

    for agent in agents:
        console.print(f"\n  Agent: [bold]{agent.name}[/bold]")
        agent_results = []

        for task in sampled:
            result = agent.run(task)
            agent_results.append(result)

            # Save result
            result.save(results_dir / agent.name / f"{task.instance_id}.json")

            status_icon = "[green]OK[/green]" if result.patch else "[red]FAIL[/red]"
            console.print(
                f"    {task.instance_id}: {status_icon} | "
                f"${result.total_cost_usd:.3f} | "
                f"{result.timestamps.e2e_time:.1f}s | "
                f"{result.convergence_steps} steps"
            )

        all_results[agent.name] = agent_results

        # Create eval results (mock: resolved if patch exists; eval_status
        # success/fail mirrors that, no environmental errors in mock).
        eval_results = [
            EvalResult(
                instance_id=r.instance_id,
                agent_name=agent.name,
                resolved=bool(r.patch),
                eval_status="success" if r.patch else "fail",
                fail_to_pass_results={
                    f"test_{r.instance_id}": bool(r.patch)
                },
                pass_to_pass_results={
                    f"test_regression_{r.instance_id}": True
                },
            )
            for r in agent_results
        ]
        all_eval_results[agent.name] = eval_results

    # Save metadata
    save_run_metadata(run_id, {
        "run_id": run_id,
        "tier": "local",
        "num_tasks": len(sampled),
        "agents": [a.name for a in agents],
        "mode": "e2e-test (mock)",
    })

    # Step 4: Compute metrics
    console.print("\n[bold]Step 4: Compute Metrics[/bold]")

    agent_metrics: dict[str, dict[str, float]] = {}

    for agent_name in all_results:
        results = all_results[agent_name]
        eval_res = all_eval_results[agent_name]
        resolved_ids = {r.instance_id for r in eval_res if r.resolved}

        metrics = {
            "task_resolution_rate": task_resolution_rate(eval_res),
            "token_efficiency": token_efficiency(results, resolved_ids),
            "cost_per_resolved_task": cost_per_resolved_task(results, resolved_ids),
            "e2e_time": avg_e2e_time(results),
            "time_to_first_action": avg_time_to_first_action(results),
            "convergence_steps": avg_convergence_steps(results),
        }

        agent_metrics[agent_name] = metrics

        console.print(f"\n  [bold]{agent_name}[/bold]:")
        for name, value in metrics.items():
            if value == float("inf"):
                console.print(f"    {name}: N/A")
            elif "rate" in name or "safety" in name:
                console.print(f"    {name}: {value*100:.1f}%")
            elif "cost" in name:
                console.print(f"    {name}: ${value:.3f}")
            elif "time" in name:
                console.print(f"    {name}: {value:.1f}s")
            else:
                console.print(f"    {name}: {value:.1f}")

    # Step 5: Score and grade
    console.print("\n[bold]Step 5: Score & Grade[/bold]")
    agent_scores = {}
    for agent_name, metrics in agent_metrics.items():
        scores = score_agent(metrics)
        agent_scores[agent_name] = scores
        console.print(f"\n  [bold]{agent_name}[/bold]:")
        for s in scores:
            val = f"{s.value:.2f}" if s.value != float("inf") else "N/A"
            console.print(f"    {s.name}: {val} {s.unit} -> [{s.grade}]")

    # Step 6: Generate reports
    console.print("\n[bold]Step 6: Generate Reports[/bold]")

    md_report = format_markdown(agent_scores, run_id, "local", len(sampled))
    md_path = save_report(md_report, run_id, "markdown", results_dir)
    console.print(f"  Markdown: {md_path}")

    json_report = format_json(agent_scores, run_id, "local", len(sampled))
    json_path = save_report(json_report, run_id, "json", results_dir)
    console.print(f"  JSON: {json_path}")

    # Print the markdown report
    console.print("\n" + "=" * 60)
    console.print(md_report)
    console.print("=" * 60)

    console.print("\n[bold green]E2E test complete! Pipeline works correctly.[/bold green]")


if __name__ == "__main__":
    main()
