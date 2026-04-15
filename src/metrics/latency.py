"""Latency metrics: E2E Completion Time and Time to First Action."""

from __future__ import annotations

from src.core.models import AgentResult


def avg_e2e_time(results: list[AgentResult]) -> float:
    """Average end-to-end completion time in seconds."""
    times = [r.timestamps.e2e_time for r in results if r.timestamps.e2e_time > 0]
    if not times:
        return 0.0
    return sum(times) / len(times)


def avg_time_to_first_action(results: list[AgentResult]) -> float:
    """Average time to first action in seconds."""
    times = [
        r.timestamps.time_to_first_action
        for r in results
        if r.timestamps.time_to_first_action > 0
    ]
    if not times:
        return 0.0
    return sum(times) / len(times)


def median_e2e_time(results: list[AgentResult]) -> float:
    times = sorted(
        r.timestamps.e2e_time for r in results if r.timestamps.e2e_time > 0
    )
    if not times:
        return 0.0
    n = len(times)
    if n % 2 == 0:
        return (times[n // 2 - 1] + times[n // 2]) / 2
    return times[n // 2]


def per_instance_latency(results: list[AgentResult]) -> list[dict]:
    return [
        {
            "instance_id": r.instance_id,
            "e2e_time": r.timestamps.e2e_time,
            "ttfa": r.timestamps.time_to_first_action,
        }
        for r in results
    ]
