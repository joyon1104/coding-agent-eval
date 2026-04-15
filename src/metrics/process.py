"""Process metrics: Convergence Steps."""

from __future__ import annotations

from src.core.models import AgentResult


def avg_convergence_steps(results: list[AgentResult]) -> float:
    """Average number of turns/steps to complete."""
    steps = [r.convergence_steps for r in results if r.convergence_steps > 0]
    if not steps:
        return 0.0
    return sum(steps) / len(steps)


def median_convergence_steps(results: list[AgentResult]) -> float:
    steps = sorted(r.convergence_steps for r in results if r.convergence_steps > 0)
    if not steps:
        return 0.0
    n = len(steps)
    if n % 2 == 0:
        return (steps[n // 2 - 1] + steps[n // 2]) / 2
    return steps[n // 2]


def per_instance_steps(results: list[AgentResult]) -> list[dict]:
    return [
        {
            "instance_id": r.instance_id,
            "convergence_steps": r.convergence_steps,
        }
        for r in results
    ]
