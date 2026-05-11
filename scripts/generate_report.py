#!/usr/bin/env python3
"""Generate evaluation report from run results."""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from pathlib import Path
from rich.console import Console

from src.core.config import PROJECT_ROOT
from src.core.models import AgentResult
from src.evaluator.swebench_harness import EvalResult
from src.metrics.accuracy import task_resolution_rate
from src.metrics.cost import avg_tokens_per_task, avg_cost_per_task, cost_per_resolved_task, token_efficiency
from src.metrics.latency import avg_e2e_time, avg_time_to_first_action
from src.metrics.process import avg_convergence_steps
from src.reporter.comparator import load_run_results, merge_results
from src.reporter.scorer import score_agent
from src.reporter.formatter import format_markdown, format_json, format_csv, save_report, save_summary

console = Console()


def compute_metrics(
    agent_results: list[AgentResult],
    eval_results: list[EvalResult] | None = None,
) -> dict[str, float]:
    """Compute all metrics for an agent.

    When Step 2 (Docker eval) hasn't been run, we synthesize stub EvalResults
    so downstream code can still produce a report. Stub status:
      - patch present → "fail" (would be evaluable but tests didn't run)
      - patch absent  → "fail" (agent-side issue: no patch produced)
    Marking these as "fail" (not "error" or "success") keeps them in the TRR
    denominator while honestly admitting the evaluation didn't really run.
    """
    if eval_results is None:
        eval_results = [
            EvalResult(
                instance_id=r.instance_id,
                agent_name=r.agent_name,
                resolved=False,
                eval_status="fail",
            )
            for r in agent_results
        ]

    resolved_ids = {r.instance_id for r in eval_results if r.resolved}

    return {
        "task_resolution_rate": task_resolution_rate(eval_results),
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
    """Generate Coding Agent Eval comparison report."""

    console.print("[bold blue]Coding Agent Eval Report Generator[/bold blue]")

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

    # Load Docker eval results if available
    eval_dir = PROJECT_ROOT / "results" / "runs" / run_id / "eval"
    eval_results_map: dict[str, list[EvalResult]] = {}
    if eval_dir.exists():
        for f in eval_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                er = EvalResult(
                    instance_id=data["instance_id"],
                    agent_name=data.get("agent_name", ""),
                    resolved=data.get("resolved", False),
                    # Old eval JSONs (pre eval_status): infer from test
                    # results presence — the same fallback rule as run_eval.py.
                    eval_status=data.get(
                        "eval_status",
                        "success" if (data.get("fail_to_pass_results")
                                      or data.get("pass_to_pass_results"))
                        else "error",
                    ),
                    fail_to_pass_results=data.get("fail_to_pass_results", {}),
                    pass_to_pass_results=data.get("pass_to_pass_results", {}),
                    error=data.get("error", ""),
                    failure_stage=data.get("failure_stage", ""),
                    failure_category=data.get("failure_category", ""),
                    root_cause=data.get("root_cause", ""),
                    details=data.get("details", {}),
                )
                eval_results_map.setdefault(er.agent_name, []).append(er)
            except (json.JSONDecodeError, KeyError):
                continue
        if eval_results_map:
            console.print(f"[dim]Docker eval results loaded: {sum(len(v) for v in eval_results_map.values())} instances[/dim]")

    for agent_name, results in all_results.items():
        console.print(f"\n[bold]{agent_name}[/bold]: {len(results)} results")
        num_tasks = max(num_tasks, len(results))

        # Use Docker eval results if available for this agent
        agent_eval = eval_results_map.get(agent_name)
        if not agent_eval:
            # Try matching by instance_id regardless of agent_name
            all_evals = [er for evals in eval_results_map.values() for er in evals]
            result_ids = {r.instance_id for r in results}
            agent_eval = [er for er in all_evals if er.instance_id in result_ids] or None

        metrics = compute_metrics(results, agent_eval)
        scores = score_agent(metrics)
        agent_scores[agent_name] = scores

        for s in scores:
            val_str = f"{s.value:.2f}" if s.value != float("inf") else "N/A"
            console.print(f"  {s.name}: {val_str} {s.unit} [{s.grade}]")

    # Determine tier from metadata
    metadata_path = PROJECT_ROOT / "results" / "runs" / run_id / "metadata.json"
    tier = "unknown"
    if metadata_path.exists():
        meta = json.loads(metadata_path.read_text())
        tier = meta.get("tier", "unknown")

    # Generate reports — use reports/ subdirectory if patches/ exists (new layout)
    run_dir = PROJECT_ROOT / "results" / "runs" / run_id
    if (run_dir / "patches").is_dir():
        output_dir = run_dir / "reports"
    else:
        output_dir = run_dir
    formats = [f.strip() for f in fmt.split(",")]

    for f in formats:
        if f == "markdown":
            all_evals_for_report = [er for evals in eval_results_map.values() for er in evals]
            content = format_markdown(
                agent_scores, run_id, tier, num_tasks,
                eval_results=all_evals_for_report or None,
            )
        elif f == "json":
            content = format_json(agent_scores, run_id, tier, num_tasks)
        elif f == "csv":
            content = format_csv(agent_scores)
        else:
            console.print(f"[yellow]Unknown format: {f}[/yellow]")
            continue

        path = save_report(content, run_id, f, output_dir)
        console.print(f"\n[green]Report saved: {path}[/green]")

    # Refresh summary.json so the dashboard picks up the latest task counts
    # and metric grades. Without this, regenerating reports leaves the
    # dashboard showing stale or buggy summary data.
    if not merge_dirs:
        meta = {}
        if metadata_path.exists():
            meta = json.loads(metadata_path.read_text())
        all_evals_flat = [er for evals in eval_results_map.values() for er in evals]
        summary_path = save_summary(
            run_dir, meta, agent_scores,
            agent_results=all_results,
            eval_results=all_evals_flat or None,
        )
        console.print(f"[green]Summary refreshed: {summary_path}[/green]")


if __name__ == "__main__":
    main()
