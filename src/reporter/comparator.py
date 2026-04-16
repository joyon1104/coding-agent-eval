"""Multi-agent comparison and result merging."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.models import AgentResult


def load_run_results(run_dir: Path) -> dict[str, list[AgentResult]]:
    """Load all results from a single run directory.

    Supports both new layout (patches/) and legacy layout ({agent_name}/).
    """
    results: dict[str, list[AgentResult]] = {}

    # New layout: patches/ directory
    patches_dir = run_dir / "patches"
    if patches_dir.is_dir():
        # Determine agent name from metadata
        agent_name = _get_agent_from_metadata(run_dir)
        results[agent_name] = _load_results_from_dir(patches_dir)
        return results

    # Legacy layout: agent-name subdirectories
    skip_dirs = {"eval", "reports", "patches", "."}
    for agent_dir in run_dir.iterdir():
        if not agent_dir.is_dir():
            continue
        if agent_dir.name in skip_dirs or agent_dir.name.startswith("."):
            continue
        agent_name = agent_dir.name
        results[agent_name] = _load_results_from_dir(agent_dir)

    return results


def merge_results(result_dirs: list[str]) -> dict[str, list[AgentResult]]:
    """Merge results from multiple run directories."""
    all_results: dict[str, list[AgentResult]] = {}

    for run_dir in result_dirs:
        run_path = Path(run_dir)
        if not run_path.exists():
            continue

        run_results = load_run_results(run_path)
        for agent_name, agent_results in run_results.items():
            all_results.setdefault(agent_name, [])
            existing_ids = {r.instance_id for r in all_results[agent_name]}
            for r in agent_results:
                if r.instance_id not in existing_ids:
                    all_results[agent_name].append(r)

    return all_results


def _load_results_from_dir(directory: Path) -> list[AgentResult]:
    """Load AgentResult files from a directory."""
    results = []
    for f in sorted(directory.glob("*.json")):
        if f.name == "metadata.json":
            continue
        try:
            result = AgentResult.from_dict(json.loads(f.read_text()))
            results.append(result)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return results


def _get_agent_from_metadata(run_dir: Path) -> str:
    """Extract agent name from metadata.json."""
    metadata_path = run_dir / "metadata.json"
    if metadata_path.exists():
        try:
            meta = json.loads(metadata_path.read_text())
            return meta.get("agent", "unknown")
        except (json.JSONDecodeError, KeyError):
            pass
    return "unknown"
