#!/usr/bin/env python3
"""Main evaluation runner CLI."""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from datetime import datetime
from rich.console import Console

from src.core.config import Config
from src.core.env_detect import detect_environment
from src.dataset.loader import load_dataset_for_tier
from src.dataset.sampler import sample_tasks
from src.adapters.claude_code import ClaudeCodeAdapter
from src.runner.orchestrator import Orchestrator

console = Console()

AGENT_REGISTRY = {
    "claude-code": ClaudeCodeAdapter,
}


@click.command()
@click.option("--tier", type=click.Choice(["micro", "mini", "full"]), default=None,
              help="Dataset tier (auto-detected if omitted)")
@click.option("--agents", default="claude-code",
              help="Comma-separated agent names")
@click.option("--run-id", default=None,
              help="Run identifier (auto-generated if omitted)")
@click.option("--sample-size", type=int, default=None,
              help="Override sample size")
@click.option("--offline", is_flag=True, default=False,
              help="Force offline mode (local dataset only)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Show what would run without executing")
def main(tier, agents, run_id, sample_size, offline, dry_run):
    """CAPE Eval — AI Coding Agent Performance Evaluation"""

    console.print("[bold blue]CAPE Eval[/bold blue] — AI Coding Agent Evaluation")
    console.print()

    # Environment detection
    env = detect_environment()
    console.print(f"[dim]{env.summary()}[/dim]")

    # Config
    config = Config(tier=tier, offline=offline)
    console.print(f"Tier: [bold]{config.tier}[/bold]")

    # Run ID
    if not run_id:
        run_id = f"eval-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    console.print(f"Run ID: [bold]{run_id}[/bold]")

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
        adapter = adapter_cls(config={
            "max_turns": config.execution_config.get("max_turns_per_task", 50),
            "max_budget": config.execution_config.get("max_budget_per_task", 5.0),
            "timeout": config.execution_config.get("max_time_per_task", 1800),
        })
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
        return

    # Run evaluation
    console.print(f"\n[bold]Starting evaluation...[/bold]")
    orchestrator = Orchestrator(config, run_id)
    results = orchestrator.run(sampled, agent_instances)

    # Summary
    console.print(f"\n[bold green]Evaluation complete![/bold green]")
    for agent_name, agent_results in results.items():
        success = sum(1 for r in agent_results if r.status.value == "success")
        total = len(agent_results)
        total_cost = sum(r.total_cost_usd for r in agent_results)
        console.print(
            f"  {agent_name}: {success}/{total} success | "
            f"Cost: ${total_cost:.3f}"
        )

    console.print(f"\nResults saved to: results/runs/{run_id}/")
    console.print(f"Generate report: python scripts/generate_report.py --run-id {run_id}")


if __name__ == "__main__":
    main()
