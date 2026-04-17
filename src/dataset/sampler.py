"""Dataset sampler: Lite/Verified/Full/Multi tier sampling."""

from __future__ import annotations

import random
from typing import Optional

from src.core.models import EvalTask

_TIER_DEFAULT_SIZE = {
    "local": 5,
    "lite": 50,
    "verified": 50,
    "full": 500,
    "multi": 50,
}


def sample_tasks(
    tasks: list[EvalTask],
    tier: str,
    sample_size: Optional[int] = None,
    seed: int = 42,
) -> list[EvalTask]:
    """Randomly sample tasks for the given tier."""
    if tier not in _TIER_DEFAULT_SIZE:
        raise ValueError(f"Unknown tier: {tier}")

    n = sample_size or _TIER_DEFAULT_SIZE[tier]
    if len(tasks) <= n:
        return tasks
    rng = random.Random(seed)
    return rng.sample(tasks, n)
