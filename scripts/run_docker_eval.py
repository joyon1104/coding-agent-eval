#!/usr/bin/env python3
"""Run Docker-based test verification on existing eval results."""

import sys
import os
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from pathlib import Path
from rich.console import Console
from rich.markup import escape as _esc

from src.core.config import PROJECT_ROOT
from src.core.models import AgentResult, EvalTask
from src.dataset.loader import load_from_jsonl
from src.evaluator.docker_evaluator import evaluate_batch, get_image_name
from src.evaluator.swebench_harness import EvalResult
from src.metrics.accuracy import task_resolution_rate

console = Console()
logger = logging.getLogger("coding-agent-eval")


def _resolve_dataset(dataset_arg, run_dir):
    """Pick the dataset JSONL: explicit flag > tier from metadata > common fallbacks."""
    if dataset_arg:
        return PROJECT_ROOT / dataset_arg

    meta_path = run_dir / "metadata.json"
    if meta_path.exists():
        try:
            tier = json.loads(meta_path.read_text()).get("tier")
            if tier:
                tier_path = PROJECT_ROOT / "data" / f"swebench_{tier}.jsonl"
                if tier_path.exists():
                    return tier_path
        except (json.JSONDecodeError, KeyError):
            pass

    for name in ("verified", "lite", "multi", "full", "local"):
        path = PROJECT_ROOT / "data" / f"swebench_{name}.jsonl"
        if path.exists():
            return path

    return PROJECT_ROOT / "data" / "swebench_verified.jsonl"


@click.command()
@click.option("--run-id", required=True, help="Run ID with existing agent results")
@click.option("--agent", default="claude-code", help="Agent name to evaluate")
@click.option("--dataset", default=None,
              help="Dataset JSONL path (default: auto-detect from run's tier)")
@click.option("--timeout", default=600, help="Timeout per task (seconds)")
def main(run_id, agent, dataset, timeout):
    """Verify agent patches using Docker-based test execution."""

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    console.print("[bold blue]Coding Agent Eval — Docker Test Verification[/bold blue]\n")

    run_dir = PROJECT_ROOT / "results" / "runs" / run_id
    if not run_dir.exists():
        console.print(f"[red]Run directory not found: {run_dir}[/red]")
        sys.exit(1)

    # 1. Load agent results — new layout uses patches/; fall back to legacy <agent>/ layout
    patches_dir = run_dir / "patches"
    legacy_dir = run_dir / agent
    if patches_dir.exists():
        results_dir = patches_dir
        layout = "patches"
    elif legacy_dir.exists():
        results_dir = legacy_dir
        layout = agent
    else:
        console.print(f"[red]No patches under {run_dir} (looked for patches/ and {agent}/)[/red]")
        sys.exit(1)

    agent_results = []
    for f in sorted(results_dir.glob("*.json")):
        if f.name == "metadata.json":
            continue
        r = AgentResult.from_dict(json.loads(f.read_text()))
        if layout == "patches" and r.agent_name and r.agent_name != agent:
            continue  # skip results from other agents sharing the same run-id
        agent_results.append(r)

    if not agent_results:
        console.print(f"[red]No agent results for agent='{agent}' in {results_dir}[/red]")
        sys.exit(1)

    console.print(f"Loaded {len(agent_results)} agent results from [bold]{run_id}/{layout}/[/bold]")

    # 2. Load dataset for task info (test_patch, FAIL_TO_PASS, etc.)
    dataset_path = _resolve_dataset(dataset, run_dir)
    if not dataset_path.exists():
        console.print(f"[red]Dataset JSONL not found: {dataset_path}[/red]")
        sys.exit(1)
    console.print(f"Dataset: [bold]{dataset_path.relative_to(PROJECT_ROOT)}[/bold]")
    raw_items = load_from_jsonl(dataset_path)
    tasks = [EvalTask.from_swebench(item) for item in raw_items]
    console.print(f"Loaded {len(tasks)} tasks from dataset")

    # Filter tasks to only those with results
    result_ids = {r.instance_id for r in agent_results}
    tasks = [t for t in tasks if t.instance_id in result_ids]
    console.print(f"Matching tasks: {len(tasks)}")

    for t in tasks:
        console.print(f"  - {t.instance_id}: F2P={len(t.FAIL_TO_PASS)} P2P={len(t.PASS_TO_PASS)}")

    # 3. Show SWE-bench Docker images to be used
    console.print(f"\n[bold]Step 1: SWE-bench Docker Images[/bold]")
    for t in tasks:
        console.print(f"  {t.instance_id} -> {get_image_name(t.instance_id)}")

    # 4. Run Docker-based evaluation (images pulled automatically)
    console.print(f"\n[bold]Step 2: Test Verification[/bold]")
    eval_results = evaluate_batch(tasks, agent_results, timeout_per_task=timeout)

    # 5. Display results
    console.print(f"\n[bold]Step 3: Results[/bold]\n")

    for er in eval_results:
        status = "[green]RESOLVED[/green]" if er.resolved else "[red]NOT RESOLVED[/red]"
        console.print(f"  {er.instance_id}: {status}")

        if er.error:
            console.print(f"    Error: {_esc(er.error)}")

        # Test names may contain square brackets (e.g. parametrized pytest ids like
        # "test_foo[param-1]") which rich interprets as markup tags and crashes on.
        # Wrap every dynamic test-name string in escape() to keep it literal.
        if er.fail_to_pass_results:
            for test, passed in er.fail_to_pass_results.items():
                icon = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
                console.print(f"    F2P: {icon} {_esc(test)}")

        if er.pass_to_pass_results:
            p2p_pass = sum(1 for v in er.pass_to_pass_results.values() if v)
            p2p_total = len(er.pass_to_pass_results)
            if p2p_pass == p2p_total:
                console.print(f"    P2P: [green]ALL PASS ({p2p_pass}/{p2p_total})[/green]")
            else:
                console.print(f"    P2P: [yellow]{p2p_pass}/{p2p_total} passed[/yellow]")
                for test, passed in er.pass_to_pass_results.items():
                    if not passed:
                        console.print(f"    P2P: [red]FAIL[/red] {_esc(test)}")

    # 6. Summary metrics
    console.print(f"\n[bold]Summary[/bold]")
    trr = task_resolution_rate(eval_results)
    counts = {"success": 0, "fail": 0, "error": 0}
    resolved = 0
    for er in eval_results:
        counts[er.eval_status] = counts.get(er.eval_status, 0) + 1
        if er.resolved:
            resolved += 1
    evaluable = counts["success"] + counts["fail"]
    console.print(f"  Tasks: success={counts['success']}  fail={counts['fail']}  error={counts['error']}")
    console.print(f"  Resolved: {resolved} / {evaluable} evaluable")
    console.print(f"  Task Resolution Rate: {trr*100:.1f}% (resolved / (success + fail))")

    # 7. Save eval results
    eval_output_dir = PROJECT_ROOT / "results" / "runs" / run_id / "eval"
    eval_output_dir.mkdir(parents=True, exist_ok=True)

    for er in eval_results:
        eval_path = eval_output_dir / f"{er.instance_id}.json"
        eval_path.write_text(json.dumps(er.to_dict(), indent=2, ensure_ascii=False))

    console.print(f"\n  Eval results saved to: {eval_output_dir}")


if __name__ == "__main__":
    main()
