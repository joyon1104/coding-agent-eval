"""Multi-agent comparison and result merging."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.models import AgentResult


def merge_results(result_dirs: list[str]) -> dict[str, list[AgentResult]]:
    """Merge results from multiple run directories."""
    all_results: dict[str, list[AgentResult]] = {}

    for run_dir in result_dirs:
        run_path = Path(run_dir)
        if not run_path.exists():
            continue

        for agent_dir in run_path.iterdir():
            if not agent_dir.is_dir():
                continue
            agent_name = agent_dir.name
            all_results.setdefault(agent_name, [])

            for f in agent_dir.glob("*.json"):
                try:
                    result = AgentResult.from_dict(json.loads(f.read_text()))
                    # Avoid duplicates
                    existing_ids = {r.instance_id for r in all_results[agent_name]}
                    if result.instance_id not in existing_ids:
                        all_results[agent_name].append(result)
                except (json.JSONDecodeError, KeyError):
                    continue

    return all_results


def load_run_results(run_dir: Path) -> dict[str, list[AgentResult]]:
    """Load all results from a single run directory."""
    results: dict[str, list[AgentResult]] = {}

    for agent_dir in run_dir.iterdir():
        if not agent_dir.is_dir() or agent_dir.name.startswith("."):
            continue
        agent_name = agent_dir.name
        results[agent_name] = []

        for f in sorted(agent_dir.glob("*.json")):
            if f.name == "metadata.json":
                continue
            try:
                result = AgentResult.from_dict(json.loads(f.read_text()))
                results[agent_name].append(result)
            except (json.JSONDecodeError, KeyError):
                continue

    return results
