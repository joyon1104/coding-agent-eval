"""Cost metrics: Token Efficiency and Cost per Resolved Task."""

from __future__ import annotations

from src.core.models import AgentResult, TaskStatus


def total_tokens(results: list[AgentResult]) -> int:
    return sum(r.token_usage.total_tokens for r in results)


def avg_tokens_per_task(results: list[AgentResult]) -> float:
    if not results:
        return 0.0
    return total_tokens(results) / len(results)


def total_cost(results: list[AgentResult]) -> float:
    return sum(r.total_cost_usd for r in results)


def avg_cost_per_task(results: list[AgentResult]) -> float:
    if not results:
        return 0.0
    return total_cost(results) / len(results)


def cost_per_resolved_task(
    results: list[AgentResult], resolved_ids: set[str]
) -> float:
    """CRT = total_cost / num_resolved. Returns inf if none resolved."""
    tc = total_cost(results)
    n_resolved = sum(1 for r in results if r.instance_id in resolved_ids)
    if n_resolved == 0:
        return float("inf")
    return tc / n_resolved


def token_efficiency(
    results: list[AgentResult], resolved_ids: set[str]
) -> float:
    """Avg tokens per resolved task. Lower is better."""
    resolved_results = [r for r in results if r.instance_id in resolved_ids]
    if not resolved_results:
        return float("inf")
    return avg_tokens_per_task(resolved_results)


def compute_cost_from_tokens(
    results: list[AgentResult], pricing: dict
) -> list[AgentResult]:
    """Recompute cost from token counts using pricing table, if cost is 0."""
    for r in results:
        if r.total_cost_usd > 0:
            continue
        model = r.model_name
        if model not in pricing:
            continue
        p = pricing[model]
        r.total_cost_usd = (
            r.token_usage.input_tokens * p.get("input_per_1m", 0) / 1_000_000
            + r.token_usage.output_tokens * p.get("output_per_1m", 0) / 1_000_000
            + r.token_usage.cache_read_tokens * p.get("cache_read_per_1m", 0) / 1_000_000
        )
    return results
