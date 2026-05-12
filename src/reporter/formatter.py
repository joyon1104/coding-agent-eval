"""Report formatters: Markdown, JSON, CSV."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.reporter.scorer import MetricScore


def _build_failure_breakdown_table(eval_results: list) -> list[str]:
    """Return markdown lines for the failure category breakdown table.

    Only called when eval_results is non-empty and contains non-success entries.
    """
    from src.evaluator.failure_classifier import is_infrastructure_failure

    category_counts: Counter = Counter()
    for er in eval_results:
        cat = getattr(er, "failure_category", "")
        if cat:
            category_counts[cat] += 1

    model_failures = sum(1 for er in eval_results
                         if getattr(er, "failure_category", "") == "model_failure")
    infra_failures = sum(
        1 for er in eval_results
        if is_infrastructure_failure(getattr(er, "failure_category", ""))
    )

    if not category_counts:
        return []

    lines = [
        "",
        "## Failure Breakdown",
        "",
        "| Failure Type | Count |",
        "|---|---|",
    ]

    label_map = {
        "model_failure": "Model failures (agent-side)",
        "environment_failure": "Environment failures",
        "network_failure": "Network / SSL / proxy failures",
        "registry_failure": "Registry / auth failures",
        "docker_failure": "Docker failures",
        "dependency_failure": "Dependency installation failures",
        "timeout": "Timeout failures",
        "quota_exceeded": "API quota / rate-limit failures",
        "configuration_error": "Configuration errors",
        "internal_error": "Internal errors",
    }

    for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
        label = label_map.get(cat, cat)
        lines.append(f"| {label} | {count} |")

    lines.extend([
        "",
        f"> Model failures: **{model_failures}** | "
        f"Infrastructure failures: **{infra_failures}**  ",
        "> Infrastructure failures should NOT count against model capability.",
    ])
    return lines


def format_markdown(
    agent_scores: dict[str, list[MetricScore]],
    run_id: str,
    tier: str,
    num_tasks: int,
    eval_results: list | None = None,
) -> str:
    """Generate markdown comparison report.

    eval_results: optional list of EvalResult objects; when provided a failure
    breakdown table is appended so infrastructure issues are clearly separated
    from model capability failures.
    """
    lines = [
        f"# Coding Agent Eval Report",
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

    # Failure breakdown (only when Step 2 results are available)
    if eval_results:
        lines.extend(_build_failure_breakdown_table(eval_results))

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

    # Build per-task section.
    # `status` field reflects the WHOLE pipeline outcome, not just Step 1:
    #   - "success": Step 2 ran tests end-to-end (regardless of pass/fail)
    #   - "fail":    agent-side issue (no patch / patch apply failed)
    #   - "error":   environmental issue (image pull, container, runner crash)
    # If no eval ran for a task, fall back to Step 1's status: a Step 1
    # ERROR is environmental (subprocess crash etc.), so map to "error";
    # a Step 1 SUCCESS without an eval just means tests didn't run yet.
    per_task = []
    counts = {"success": 0, "fail": 0, "error": 0, "resolved": 0}
    if agent_results:
        for agent_name, results in agent_results.items():
            for r in results:
                step1_status = r.status.value if hasattr(r.status, "value") else r.status
                er = None
                if eval_results:
                    er = next(
                        (e for e in eval_results if e.instance_id == r.instance_id),
                        None,
                    )

                # Pipeline-level status
                if er is not None:
                    pipeline_status = er.eval_status
                elif step1_status == "error":
                    pipeline_status = "error"
                elif r.patch:
                    pipeline_status = "fail"  # patch exists but tests didn't run
                else:
                    pipeline_status = "fail"  # no patch produced

                counts[pipeline_status] = counts.get(pipeline_status, 0) + 1

                task_info = {
                    "instance_id": r.instance_id,
                    "agent": agent_name,
                    "status": pipeline_status,
                    "step1_status": step1_status,
                    "patch_generated": bool(r.patch),
                    "cost_usd": r.total_cost_usd,
                    "e2e_time": r.timestamps.e2e_time,
                    "tokens": r.token_usage.total_tokens,
                    "convergence_steps": r.convergence_steps,
                    "model": r.model_name,
                }

                if er is not None:
                    task_info["resolved"] = er.resolved
                    task_info["fail_to_pass_total"] = len(er.fail_to_pass_results)
                    task_info["fail_to_pass_passed"] = sum(1 for v in er.fail_to_pass_results.values() if v)
                    task_info["pass_to_pass_total"] = len(er.pass_to_pass_results)
                    task_info["pass_to_pass_passed"] = sum(1 for v in er.pass_to_pass_results.values() if v)
                    task_info["eval_detail"] = f"eval/{r.instance_id}.json"
                    if er.resolved:
                        counts["resolved"] += 1

                per_task.append(task_info)

    # Task-status tallies for the dashboard's detail page. `evaluable` is the
    # TRR denominator (success + fail). `trr_pct` is what the dashboard
    # displays alongside the formula: resolved / evaluable.
    evaluable = counts["success"] + counts["fail"]
    trr_pct = (counts["resolved"] / evaluable * 100) if evaluable > 0 else 0.0

    # Failure breakdown by category (Step 2)
    failure_breakdown: dict[str, int] = {}
    if eval_results:
        from src.evaluator.failure_classifier import is_infrastructure_failure
        for er in eval_results:
            cat = getattr(er, "failure_category", "")
            if cat:
                failure_breakdown[cat] = failure_breakdown.get(cat, 0) + 1

    model_failures = failure_breakdown.get("model_failure", 0)
    infra_failures = sum(
        v for k, v in failure_breakdown.items()
        if is_infrastructure_failure(k)
    ) if failure_breakdown else 0

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
        "task_counts": {
            "success": counts["success"],
            "fail": counts["fail"],
            "error": counts["error"],
            "resolved": counts["resolved"],
            "evaluable": evaluable,
            "resolution_rate_pct": round(trr_pct, 1),
        },
        "failure_breakdown": {
            "by_category": failure_breakdown,
            "model_failures": model_failures,
            "infrastructure_failures": infra_failures,
        },
        "per_task": per_task,
        "generated_at": datetime.now().isoformat(),
    }

    path = run_dir / "summary.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return path
