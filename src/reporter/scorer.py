"""Score and grade calculation for agents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MetricScore:
    name: str
    value: float
    unit: str
    grade: str  # S, A, B, C, D, F
    higher_is_better: bool = True


# Grading thresholds for each metric
GRADE_THRESHOLDS = {
    "task_resolution_rate": {
        "higher_is_better": True,
        "unit": "%",
        "thresholds": {"S": 0.60, "A": 0.45, "B": 0.30, "C": 0.20, "D": 0.10},
    },
    "token_efficiency": {
        "higher_is_better": False,
        "unit": "tokens/task",
        "thresholds": {"S": 50000, "A": 100000, "B": 200000, "C": 400000, "D": 800000},
    },
    "cost_per_resolved_task": {
        "higher_is_better": False,
        "unit": "USD",
        "thresholds": {"S": 0.50, "A": 1.0, "B": 2.0, "C": 5.0, "D": 10.0},
    },
    "e2e_time": {
        "higher_is_better": False,
        "unit": "sec",
        "thresholds": {"S": 60, "A": 120, "B": 300, "C": 600, "D": 1200},
    },
    "time_to_first_action": {
        "higher_is_better": False,
        "unit": "sec",
        "thresholds": {"S": 3, "A": 5, "B": 10, "C": 20, "D": 30},
    },
    "convergence_steps": {
        "higher_is_better": False,
        "unit": "steps",
        "thresholds": {"S": 5, "A": 10, "B": 20, "C": 30, "D": 50},
    },
}


def grade_metric(metric_name: str, value: float) -> str:
    """Assign a grade (S/A/B/C/D/F) based on thresholds."""
    config = GRADE_THRESHOLDS.get(metric_name)
    if not config:
        return "N/A"

    thresholds = config["thresholds"]
    higher_is_better = config["higher_is_better"]

    if higher_is_better:
        for grade in ["S", "A", "B", "C", "D"]:
            if value >= thresholds[grade]:
                return grade
    else:
        for grade in ["S", "A", "B", "C", "D"]:
            if value <= thresholds[grade]:
                return grade

    return "F"


def score_agent(metrics: dict[str, float]) -> list[MetricScore]:
    """Score all metrics for an agent."""
    scores = []
    for name, value in metrics.items():
        config = GRADE_THRESHOLDS.get(name, {})
        grade = grade_metric(name, value)
        scores.append(MetricScore(
            name=name,
            value=value,
            unit=config.get("unit", ""),
            grade=grade,
            higher_is_better=config.get("higher_is_better", True),
        ))
    return scores
