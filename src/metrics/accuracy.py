"""Accuracy metrics: Task Resolution Rate and Regression Safety."""

from __future__ import annotations

from src.evaluator.swebench_harness import EvalResult


def task_resolution_rate(eval_results: list[EvalResult]) -> float:
    """TRR = resolved / total"""
    if not eval_results:
        return 0.0
    resolved = sum(1 for r in eval_results if r.resolved)
    return resolved / len(eval_results)


def regression_safety(eval_results: list[EvalResult]) -> float:
    """Fraction of instances where all PASS_TO_PASS tests still pass."""
    if not eval_results:
        return 1.0
    safe = sum(1 for r in eval_results if r.regression_safe)
    return safe / len(eval_results)


def per_instance_accuracy(eval_results: list[EvalResult]) -> list[dict]:
    """Per-instance accuracy details."""
    return [
        {
            "instance_id": r.instance_id,
            "resolved": r.resolved,
            "regression_safe": r.regression_safe,
            "fail_to_pass_rate": r.fail_to_pass_rate,
            "pass_to_pass_rate": r.pass_to_pass_rate,
        }
        for r in eval_results
    ]
