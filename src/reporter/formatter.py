"""Report formatters: Markdown, JSON, CSV."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path

from src.reporter.scorer import MetricScore


def format_markdown(
    agent_scores: dict[str, list[MetricScore]],
    run_id: str,
    tier: str,
    num_tasks: int,
) -> str:
    """Generate markdown comparison report."""
    lines = [
        f"# CAPE Eval Report",
        f"",
        f"- **Run ID**: {run_id}",
        f"- **Tier**: {tier}",
        f"- **Tasks**: {num_tasks}",
        f"- **Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"",
        f"## Metric Comparison",
        f"",
    ]

    if not agent_scores:
        lines.append("No results available.")
        return "\n".join(lines)

    # Build comparison table
    agents = list(agent_scores.keys())
    # Get all metric names
    all_metrics = []
    for scores in agent_scores.values():
        for s in scores:
            if s.name not in all_metrics:
                all_metrics.append(s.name)

    # Header
    header = "| Metric | " + " | ".join(agents) + " |"
    separator = "|--------|" + "|".join(["--------"] * len(agents)) + "|"
    lines.extend([header, separator])

    for metric_name in all_metrics:
        row = f"| {metric_name} |"
        for agent in agents:
            score = next(
                (s for s in agent_scores[agent] if s.name == metric_name), None
            )
            if score:
                if score.value == float("inf"):
                    row += f" N/A ({score.grade}) |"
                elif isinstance(score.value, float):
                    if "rate" in metric_name or "safety" in metric_name:
                        row += f" {score.value*100:.1f}% ({score.grade}) |"
                    elif "cost" in metric_name:
                        row += f" ${score.value:.3f} ({score.grade}) |"
                    elif "time" in metric_name:
                        row += f" {score.value:.1f}s ({score.grade}) |"
                    else:
                        row += f" {score.value:.1f} ({score.grade}) |"
                else:
                    row += f" {score.value} ({score.grade}) |"
            else:
                row += " - |"
        lines.append(row)

    lines.extend(["", "## Grade Legend", ""])
    lines.append("S = Excellent | A = Good | B = Average | C = Below Average | D = Poor | F = Failing")

    return "\n".join(lines)


def format_json(
    agent_scores: dict[str, list[MetricScore]],
    run_id: str,
    tier: str,
    num_tasks: int,
) -> str:
    """Generate JSON report."""
    report = {
        "run_id": run_id,
        "tier": tier,
        "num_tasks": num_tasks,
        "generated_at": datetime.now().isoformat(),
        "agents": {},
    }

    for agent, scores in agent_scores.items():
        report["agents"][agent] = {
            s.name: {
                "value": s.value if s.value != float("inf") else None,
                "unit": s.unit,
                "grade": s.grade,
            }
            for s in scores
        }

    return json.dumps(report, indent=2, ensure_ascii=False)


def format_csv(
    agent_scores: dict[str, list[MetricScore]],
) -> str:
    """Generate CSV report."""
    output = io.StringIO()
    writer = csv.writer(output)

    agents = list(agent_scores.keys())
    all_metrics = []
    for scores in agent_scores.values():
        for s in scores:
            if s.name not in all_metrics:
                all_metrics.append(s.name)

    writer.writerow(["metric"] + agents)

    for metric_name in all_metrics:
        row = [metric_name]
        for agent in agents:
            score = next(
                (s for s in agent_scores[agent] if s.name == metric_name), None
            )
            row.append(f"{score.value:.4f}" if score else "")
        writer.writerow(row)

    return output.getvalue()


def save_report(
    content: str, run_id: str, fmt: str, output_dir: Path
):
    """Save report to file.

    New layout: output_dir is run_dir/reports/, file is report.{ext}
    Legacy: output_dir is run_dir, file is report_{run_id}.{ext}
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ext = {"markdown": "md", "json": "json", "csv": "csv"}.get(fmt, fmt)
    # New layout: reports/ subdirectory with simple names
    if output_dir.name == "reports":
        path = output_dir / f"report.{ext}"
    else:
        path = output_dir / f"report_{run_id}.{ext}"
    path.write_text(content, encoding="utf-8")
    return path


def save_summary(
    run_dir: Path,
    metadata: dict,
    agent_scores: dict[str, list[MetricScore]],
    agent_results: dict[str, list] | None = None,
    eval_results: list | None = None,
):
    """Save summary.json — a single dashboard-ready file.

    Contains metadata, metrics/grades, and per-task details.
    """
    # Build metrics section
    agents_data = {}
    for agent_name, scores in agent_scores.items():
        agents_data[agent_name] = {
            "metrics": {
                s.name: {
                    "value": s.value if s.value != float("inf") else None,
                    "unit": s.unit,
                    "grade": s.grade,
                }
                for s in scores
            }
        }

    # Build per-task section
    per_task = []
    if agent_results:
        for agent_name, results in agent_results.items():
            for r in results:
                task_info = {
                    "instance_id": r.instance_id,
                    "agent": agent_name,
                    "status": r.status.value if hasattr(r.status, "value") else r.status,
                    "patch_generated": bool(r.patch),
                    "cost_usd": r.total_cost_usd,
                    "e2e_time": r.timestamps.e2e_time,
                    "tokens": r.token_usage.total_tokens,
                    "convergence_steps": r.convergence_steps,
                    "model": r.model_name,
                }

                # Add eval info if available
                if eval_results:
                    er = next(
                        (e for e in eval_results if e.instance_id == r.instance_id),
                        None,
                    )
                    if er:
                        task_info["resolved"] = er.resolved
                        task_info["regression_safe"] = er.regression_safe
                        task_info["fail_to_pass_rate"] = er.fail_to_pass_rate
                        task_info["pass_to_pass_rate"] = er.pass_to_pass_rate

                per_task.append(task_info)

    summary = {
        "run_id": metadata.get("run_id", ""),
        "agent": metadata.get("agent", ""),
        "model": metadata.get("model", ""),
        "tier": metadata.get("tier", ""),
        "num_tasks": metadata.get("num_tasks", 0),
        "started_at": metadata.get("started_at", ""),
        "completed_at": metadata.get("completed_at", ""),
        "environment": metadata.get("environment", ""),
        "agents": agents_data,
        "per_task": per_task,
        "generated_at": datetime.now().isoformat(),
    }

    path = run_dir / "summary.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return path
