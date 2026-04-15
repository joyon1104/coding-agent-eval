"""Dataset sampler: Micro/Mini/Full tier sampling."""

from __future__ import annotations

import random
from typing import Optional

from src.core.models import EvalTask


def create_micro_dataset(
    tasks: list[EvalTask], n: int = 10, seed: int = 42
) -> list[EvalTask]:
    """Select n django-only instances from the dataset.
    Difficulty distribution: easy 4, medium 4, hard 2.
    """
    rng = random.Random(seed)

    django_only = [t for t in tasks if "django" in t.repo.lower()]

    if not django_only:
        # Fallback: use all tasks if no django found
        django_only = tasks

    by_diff: dict[str, list[EvalTask]] = {"easy": [], "medium": [], "hard": []}
    for task in django_only:
        bucket = by_diff.get(task.difficulty, by_diff["medium"])
        bucket.append(task)

    sampled: list[EvalTask] = []
    for diff, count in [("easy", 4), ("medium", 4), ("hard", 2)]:
        pool = by_diff[diff]
        if pool:
            sampled.extend(rng.sample(pool, min(count, len(pool))))

    # If we don't have enough from difficulty-based sampling, fill from remainder
    if len(sampled) < n:
        remaining = [t for t in django_only if t not in sampled]
        rng.shuffle(remaining)
        sampled.extend(remaining[: n - len(sampled)])

    return sampled[:n]


def sample_tasks(
    tasks: list[EvalTask],
    tier: str,
    sample_size: Optional[int] = None,
    seed: int = 42,
) -> list[EvalTask]:
    """Sample tasks based on tier."""
    if tier == "micro":
        n = sample_size or 10
        return create_micro_dataset(tasks, n=n, seed=seed)
    elif tier == "mini":
        n = sample_size or 50
        if len(tasks) <= n:
            return tasks
        rng = random.Random(seed)
        return rng.sample(tasks, n)
    elif tier == "full":
        n = sample_size or 500
        if len(tasks) <= n:
            return tasks
        rng = random.Random(seed)
        return rng.sample(tasks, n)
    else:
        raise ValueError(f"Unknown tier: {tier}")
