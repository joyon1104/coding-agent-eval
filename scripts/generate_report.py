#!/usr/bin/env python3
"""Generate evaluation report from run results."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from pathlib import Path
from rich.console import Console

from src.core.config import PROJECT_ROOT
from src.core.models import AgentResult
from src.evaluator.swebench_harness import EvalResult
from src.metrics.accuracy import task_resolution_rate, regression_safety
from src.metrics.cost import avg_tokens_per_task, avg_cost_per_task, cost_per_resolved_task, token_efficiency
from src.metrics.latency import avg_e2e_time, avg_time_to_first_action
from src.metrics.process import avg_convergence_steps
from src.reporter.comparator import load_run_results, merge_results
from src.reporter.scorer import score_agent
from src.reporter.formatter import format_markdown, format_json, format_csv, save_report

console = Console()


def compute_metrics(
    agent_results: list[AgentResult],
    eval_results: list[EvalResult] | None = None,
) -> dict[str, float]:
    """Compute all 7 metrics for an agent."""
    # If no eval results, create stub results based on patch presence
    if eval_results is None:
        eval_results = [
            EvalResult(
                instance_id=r.instance_id,
                agent_name=r.agent_name,
                resolved=bool(r.patch),  # Simplified: has patch = "resolved"
            )
            for r in agent_results
        ]

    resolved_ids = {r.instance_id for r in eval_results if r.resolved}

    return {
        "task_resolution_rate": task_resolution_rate(eval_results),
        "regression_safety": regression_safety(eval_results),
        "token_efficiency": token_efficiency(agent_results, resolved_ids),
        "cost_per_resolved_task": cost_per_resolved_task(agent_results, resolved_ids),
        "e2e_time": avg_e2e_time(agent_results),
        "time_to_first_action": avg_time_to_first_action(agent_results),
        "convergence_steps": avg_convergence_steps(agent_results),
    }


@click.command()
@click.option("--run-id", required=True, help="Run ID to generate report for")
@click.option("--format", "fmt", default="markdown,json",
              help="Output formats (comma-separated: markdown,json,csv)")
@click.option("--merge-dirs", default=None,
              help="Comma-separated dirs to merge results from")
def main(run_id, fmt, merge_dirs):
    """Generate CAPE Eval comparison report."""

    console.print("[bold blue]CAPE Eval Report Generator[/bold blue]")

    # Load results
    if merge_dirs:
        dirs = [d.strip() for d in merge_dirs.split(",")]
        all_results = merge_results(dirs)
    else:
        run_dir = PROJECT_ROOT / "results" / "runs" / run_id
        if not run_dir.exists():
            console.print(f"[red]Run directory not found: {run_dir}[/red]")
            sys.exit(1)
        all_results = load_run_results(run_dir)

    if not all_results:
        console.print("[red]No results found.[/red]")
        sys.exit(1)

    # Compute metrics and scores for each agent
    agent_scores = {}
    num_tasks = 0

    for agent_name, results in all_results.items():
        console.print(f"\n[bold]{agent_name}[/bold]: {len(results)} results")
        num_tasks = max(num_tasks, len(results))

        metrics = compute_metrics(results)
        scores = score_agent(metrics)
        agent_scores[agent_name] = scores

        for s in scores:
            val_str = f"{s.value:.2f}" if s.value != float("inf") else "N/A"
            console.print(f"  {s.name}: {val_str} {s.unit} [{s.grade}]")

    # Determine tier from metadata
    metadata_path = PROJECT_ROOT / "results" / "runs" / run_id / "metadata.json"
    tier = "unknown"
    if metadata_path.exists():
        import json
        meta = json.loads(metadata_path.read_text())
        tier = meta.get("tier", "unknown")

    # Generate reports
    output_dir = PROJECT_ROOT / "results" / "runs" / run_id
    formats = [f.strip() for f in fmt.split(",")]

    for f in formats:
        if f == "markdown":
            content = format_markdown(agent_scores, run_id, tier, num_tasks)
        elif f == "json":
            content = format_json(agent_scores, run_id, tier, num_tasks)
        elif f == "csv":
            content = format_csv(agent_scores)
        else:
            console.print(f"[yellow]Unknown format: {f}[/yellow]")
            continue

        path = save_report(content, run_id, f, output_dir)
        console.print(f"\n[green]Report saved: {path}[/green]")


if __name__ == "__main__":
    main()
