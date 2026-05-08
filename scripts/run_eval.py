#!/usr/bin/env python3
"""Main evaluation runner CLI."""

import sys
import os
import json
import logging

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from datetime import datetime
from rich.console import Console

from src.core.config import Config, PROJECT_ROOT
from src.core.env_detect import detect_environment
from src.core.run_id import generate_run_id
from src.dataset.loader import load_dataset_for_tier, load_from_jsonl
from src.dataset.sampler import sample_tasks
from src.adapters.claude_code import ClaudeCodeAdapter
from src.adapters.opencode import OpenCodeAdapter
from src.runner.orchestrator import Orchestrator

console = Console()

AGENT_REGISTRY = {
    "claude-code": ClaudeCodeAdapter,
    "opencode": OpenCodeAdapter,
}


@click.command()
@click.option("--tier", type=click.Choice(["local", "lite", "verified", "full", "multi"]), default=None,
              help="Dataset tier (auto-detected if omitted)")
@click.option("--agents", default="claude-code",
              help="Comma-separated agent names")
@click.option("--run-id", default=None,
              help="Run identifier (auto-generated if omitted)")
@click.option("--sample-size", type=int, default=None,
              help="Override sample size")
@click.option("--offline", is_flag=True, default=False,
              help="Force offline mode (local dataset only)")
@click.option("--model", default=None,
              help="Model to use (e.g. sonnet, opus, claude-sonnet-4-6)")
@click.option("--verify", is_flag=True, default=False,
              help="Run Docker test verification and generate report after evaluation")
@click.option("--dataset", default=None,
              help="Dataset JSONL path for Docker verification (default: auto-detect)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Show what would run without executing")
@click.option("--claude-code-vllm", is_flag=True, default=False,
              help="Run Claude Code against a vLLM-backed Anthropic-compatible endpoint "
                   "(requires CLAUDE_CODE_VLLM_BASE_URL, CLAUDE_CODE_VLLM_AUTH_TOKEN, "
                   "CLAUDE_CODE_VLLM_MODEL in .env or environment)")
def main(tier, agents, run_id, sample_size, offline, model, verify, dataset, dry_run,
         claude_code_vllm):
    """Coding Agent Eval — AI Coding Agent Performance Evaluation"""

    console.print("[bold blue]Coding Agent Eval[/bold blue] — AI Coding Agent Evaluation")
    console.print()

    # Environment detection
    env = detect_environment()
    console.print(f"[dim]{env.summary()}[/dim]")

    # Config
    config = Config(tier=tier, offline=offline)
    console.print(f"Tier: [bold]{config.tier}[/bold]")

    # Determine primary agent for run-id generation
    primary_agent = [a.strip() for a in agents.split(",")][0]

    # Run ID — auto-generated if omitted; if provided and results/runs/<run-id>/
    # already exists, the orchestrator resumes from where it left off.
    if not run_id:
        run_id = generate_run_id(primary_agent, model)
    console.print(f"Run ID: [bold]{run_id}[/bold]")

    run_dir = PROJECT_ROOT / "results" / "runs" / run_id
    if run_dir.exists():
        console.print(f"[dim]Existing run directory found; resuming from saved results[/dim]")

    # Load dataset
    console.print("\nLoading dataset...")
    tasks = load_dataset_for_tier(config)
    console.print(f"  Loaded: {len(tasks)} instances")

    # Sample
    effective_size = sample_size or config.tier_config.get("sample_size", len(tasks))
    sampled = sample_tasks(tasks, config.tier, sample_size=effective_size)
    console.print(f"  Sampled: {len(sampled)} instances")

    # Initialize agents
    agent_names = [a.strip() for a in agents.split(",")]
    agent_instances = []
    for name in agent_names:
        if name not in AGENT_REGISTRY:
            console.print(f"  [red]Unknown agent: {name}[/red]")
            continue
        adapter_cls = AGENT_REGISTRY[name]
        agent_config = {
            "max_turns": config.execution_config.get("max_turns_per_task", 50),
            "max_budget": config.execution_config.get("max_budget_per_task", 5.0),
            "timeout": config.execution_config.get("max_time_per_task", 1800),
        }
        if model:
            agent_config["model"] = model
        if claude_code_vllm and name == "claude-code":
            agent_config["vllm_mode"] = True
        try:
            adapter = adapter_cls(config=agent_config)
        except ValueError as exc:
            console.print(f"  [red]Agent config error ({name}): {exc}[/red]")
            sys.exit(1)
        if adapter.is_available():
            agent_instances.append(adapter)
            console.print(f"  [green]Agent ready: {name}[/green]")
        else:
            console.print(f"  [yellow]Agent not available: {name}[/yellow]")

    if not agent_instances:
        console.print("[red]No agents available. Exiting.[/red]")
        sys.exit(1)

    if dry_run:
        console.print("\n[yellow]Dry run — would execute:[/yellow]")
        for agent in agent_instances:
            console.print(f"  Agent: {agent.name}")
        for task in sampled[:5]:
            console.print(f"  Task: {task.instance_id} ({task.repo})")
        if len(sampled) > 5:
            console.print(f"  ... and {len(sampled) - 5} more")
        if verify:
            console.print(f"  [dim]--verify: Docker test verification + report will run after[/dim]")
        return

    # ── Step 1: Run agents ──
    console.print(f"\n[bold]Step 1: Agent Evaluation[/bold]")

    # Build extra metadata for backend tracking (used by dashboard / reports).
    extra_metadata: dict = {}
    primary_adapter = agent_instances[0] if agent_instances else None
    if primary_adapter and hasattr(primary_adapter, "backend"):
        extra_metadata["claude_code_backend"] = primary_adapter.backend
        if primary_adapter.backend == "vllm" and hasattr(primary_adapter, "vllm_model"):
            extra_metadata["claude_code_vllm_model"] = primary_adapter.vllm_model

    orchestrator = Orchestrator(config, run_id, model=model, extra_metadata=extra_metadata)
    results = orchestrator.run(sampled, agent_instances)

    console.print(f"\n[bold green]Agent evaluation complete![/bold green]")
    for agent_name, agent_results in results.items():
        success = sum(1 for r in agent_results if r.status.value == "success")
        total = len(agent_results)
        total_cost = sum(r.total_cost_usd for r in agent_results)
        console.print(
            f"  {agent_name}: {success}/{total} success | "
            f"Cost: ${total_cost:.3f}"
        )

    if not verify:
        console.print(f"\nResults saved to: results/runs/{run_id}/")
        console.print(f"To verify & report: python scripts/run_eval.py --run-id {run_id} --verify")
        return

    # ── Step 2: Docker test verification ──
    console.print(f"\n[bold]Step 2: Docker Test Verification[/bold]")

    from src.evaluator.docker_evaluator import evaluate_batch, get_image_name
    from src.core.models import AgentResult, EvalTask

    # Load full dataset with test_patch, F2P, P2P info
    dataset_path = _find_dataset(dataset, config)
    raw_items = load_from_jsonl(dataset_path)
    full_tasks = [EvalTask.from_swebench(item) for item in raw_items]

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    for agent_name, agent_results in results.items():
        result_ids = {r.instance_id for r in agent_results}
        matching_tasks = [t for t in full_tasks if t.instance_id in result_ids]

        if not matching_tasks:
            console.print(f"  [yellow]{agent_name}: No matching tasks for Docker verification[/yellow]")
            continue

        console.print(f"\n  Verifying [bold]{agent_name}[/bold] ({len(matching_tasks)} tasks)...")
        for t in matching_tasks:
            console.print(f"    {t.instance_id} -> {get_image_name(t.instance_id, tier or 'lite')}")

        eval_results = evaluate_batch(
            matching_tasks, agent_results, timeout_per_task=600, tier=tier or "lite"
        )

        # Save eval results
        eval_dir = PROJECT_ROOT / "results" / "runs" / run_id / "eval"
        eval_dir.mkdir(parents=True, exist_ok=True)
        for er in eval_results:
            eval_path = eval_dir / f"{er.instance_id}.json"
            eval_path.write_text(json.dumps(er.to_dict(), indent=2, ensure_ascii=False))

        # Show results
        resolved = sum(1 for er in eval_results if er.resolved)
        console.print(f"  {agent_name}: {resolved}/{len(eval_results)} resolved")
        for er in eval_results:
            status = "[green]RESOLVED[/green]" if er.resolved else "[red]NOT RESOLVED[/red]"
            console.print(f"    {er.instance_id}: {status}")
            if er.error:
                console.print(f"      Error: {er.error}")

    # ── Step 3: Generate report ──
    console.print(f"\n[bold]Step 3: Report Generation[/bold]")

    from src.evaluator.swebench_harness import EvalResult
    from src.metrics.accuracy import task_resolution_rate
    from src.metrics.cost import token_efficiency, cost_per_resolved_task
    from src.metrics.latency import avg_e2e_time, avg_time_to_first_action
    from src.metrics.process import avg_convergence_steps
    from src.reporter.scorer import score_agent
    from src.reporter.formatter import format_markdown, format_json, save_report, save_summary
    from src.reporter.comparator import load_run_results

    run_dir = PROJECT_ROOT / "results" / "runs" / run_id
    all_results = load_run_results(run_dir)

    # Load eval results
    eval_dir = run_dir / "eval"
    all_eval_results = []
    if eval_dir.exists():
        for f in eval_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                all_eval_results.append(EvalResult(
                    instance_id=data["instance_id"],
                    agent_name=data.get("agent_name", ""),
                    resolved=data.get("resolved", False),
                    # Old eval JSONs (pre eval_status) → infer from presence of
                    # test results: tests ran ⇒ success; otherwise fall back
                    # to error so the field is never silently wrong.
                    eval_status=data.get(
                        "eval_status",
                        "success" if (data.get("fail_to_pass_results")
                                      or data.get("pass_to_pass_results"))
                        else "error",
                    ),
                    fail_to_pass_results=data.get("fail_to_pass_results", {}),
                    pass_to_pass_results=data.get("pass_to_pass_results", {}),
                    error=data.get("error", ""),
                ))
            except (json.JSONDecodeError, KeyError):
                continue

    agent_scores = {}
    num_tasks = 0

    for agent_name, agent_results in all_results.items():
        num_tasks = max(num_tasks, len(agent_results))

        # Match eval results
        result_ids = {r.instance_id for r in agent_results}
        agent_eval = [er for er in all_eval_results if er.instance_id in result_ids] or None

        # If no eval results, synthesize stubs marked "fail" so TRR is honest
        # (Step 2 hasn't actually verified anything; treating these as
        # "resolved" would inflate the metric).
        if agent_eval is None:
            agent_eval = [
                EvalResult(
                    instance_id=r.instance_id,
                    agent_name=r.agent_name,
                    resolved=False,
                    eval_status="fail",
                )
                for r in agent_results
            ]

        resolved_ids = {r.instance_id for r in agent_eval if r.resolved}

        metrics = {
            "task_resolution_rate": task_resolution_rate(agent_eval),
            "token_efficiency": token_efficiency(agent_results, resolved_ids),
            "cost_per_resolved_task": cost_per_resolved_task(agent_results, resolved_ids),
            "e2e_time": avg_e2e_time(agent_results),
            "time_to_first_action": avg_time_to_first_action(agent_results),
            "convergence_steps": avg_convergence_steps(agent_results),
        }

        scores = score_agent(metrics)
        agent_scores[agent_name] = scores

        console.print(f"\n  [bold]{agent_name}[/bold]:")
        for s in scores:
            val_str = f"{s.value:.2f}" if s.value != float("inf") else "N/A"
            console.print(f"    {s.name}: {val_str} {s.unit} [{s.grade}]")

    # Determine tier
    metadata_path = run_dir / "metadata.json"
    report_tier = "unknown"
    if metadata_path.exists():
        meta = json.loads(metadata_path.read_text())
        report_tier = meta.get("tier", "unknown")

    # Save reports to reports/ subdirectory
    reports_dir = run_dir / "reports"
    md_report = format_markdown(agent_scores, run_id, report_tier, num_tasks)
    md_path = save_report(md_report, run_id, "markdown", reports_dir)
    console.print(f"\n  Markdown: {md_path}")

    json_report = format_json(agent_scores, run_id, report_tier, num_tasks)
    json_path = save_report(json_report, run_id, "json", reports_dir)
    console.print(f"  JSON: {json_path}")

    # Save summary.json for dashboard
    meta = {}
    if metadata_path.exists():
        meta = json.loads(metadata_path.read_text())
    summary_path = save_summary(
        run_dir, meta, agent_scores,
        agent_results=all_results,
        eval_results=all_eval_results or None,
    )
    console.print(f"  Summary: {summary_path}")

    # Print report
    console.print(f"\n{'=' * 60}")
    console.print(md_report)
    console.print(f"{'=' * 60}")

    console.print(f"\n[bold green]Full evaluation complete![/bold green]")


def _find_dataset(dataset_arg, config):
    """Find the best dataset file for Docker verification."""
    if dataset_arg:
        return PROJECT_ROOT / dataset_arg

    # Prefer the current tier's local JSONL, then fall back to any other tier file.
    tier_path = PROJECT_ROOT / config.tier_config.get("local_path", "")
    if tier_path.exists():
        return tier_path

    for name in ("verified", "lite", "multi", "full", "local"):
        path = PROJECT_ROOT / "data" / f"swebench_{name}.jsonl"
        if path.exists():
            return path

    return tier_path


if __name__ == "__main__":
    main()
