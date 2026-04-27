"""Accuracy metrics: Task Resolution Rate."""

from __future__ import annotations

from src.evaluator.swebench_harness import EvalResult


def task_resolution_rate(eval_results: list[EvalResult]) -> float:
    """TRR = resolved / (success + fail).

    Tasks with eval_status="error" (environmental failures the agent can't be
    blamed for) are excluded from the denominator. A task is `resolved` only
    when ALL F2P and ALL P2P tests pass — the strictest definition.
    """
    if not eval_results:
        return 0.0
    evaluable = [r for r in eval_results if r.eval_status in ("success", "fail")]
    if not evaluable:
        return 0.0
    resolved = sum(1 for r in evaluable if r.resolved)
    return resolved / len(evaluable)


def per_instance_accuracy(eval_results: list[EvalResult]) -> list[dict]:
    """Per-instance accuracy details."""
    return [
        {
            "instance_id": r.instance_id,
            "resolved": r.resolved,
            "eval_status": r.eval_status,
            "fail_to_pass_rate": r.fail_to_pass_rate,
            "pass_to_pass_rate": r.pass_to_pass_rate,
        }
        for r in eval_results
    ]


def status_counts(eval_results: list[EvalResult]) -> dict[str, int]:
    """Tally success/fail/error/resolved across a run for dashboard display."""
    counts = {"success": 0, "fail": 0, "error": 0, "resolved": 0}
    for r in eval_results:
        counts[r.eval_status] = counts.get(r.eval_status, 0) + 1
        if r.resolved:
            counts["resolved"] += 1
    return counts
