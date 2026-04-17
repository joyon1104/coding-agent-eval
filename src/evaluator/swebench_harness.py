"""SWE-bench test harness wrapper for evaluation."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.core.models import AgentResult, EvalTask

logger = logging.getLogger("coding-agent-eval")


@dataclass
class EvalResult:
    instance_id: str
    agent_name: str
    resolved: bool = False
    fail_to_pass_results: dict[str, bool] = None  # test_name -> passed
    pass_to_pass_results: dict[str, bool] = None  # test_name -> passed
    error: str = ""

    def __post_init__(self):
        if self.fail_to_pass_results is None:
            self.fail_to_pass_results = {}
        if self.pass_to_pass_results is None:
            self.pass_to_pass_results = {}

    @property
    def regression_safe(self) -> bool:
        """All PASS_TO_PASS tests still pass."""
        if not self.pass_to_pass_results:
            return True
        return all(self.pass_to_pass_results.values())

    @property
    def fail_to_pass_rate(self) -> float:
        if not self.fail_to_pass_results:
            return 0.0
        passed = sum(1 for v in self.fail_to_pass_results.values() if v)
        return passed / len(self.fail_to_pass_results)

    @property
    def pass_to_pass_rate(self) -> float:
        if not self.pass_to_pass_results:
            return 1.0
        passed = sum(1 for v in self.pass_to_pass_results.values() if v)
        return passed / len(self.pass_to_pass_results)

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "agent_name": self.agent_name,
            "resolved": self.resolved,
            "fail_to_pass_results": self.fail_to_pass_results,
            "pass_to_pass_results": self.pass_to_pass_results,
            "regression_safe": self.regression_safe,
            "fail_to_pass_rate": self.fail_to_pass_rate,
            "pass_to_pass_rate": self.pass_to_pass_rate,
            "error": self.error,
        }


def run_swebench_evaluation(
    predictions: list[dict],
    dataset_name: str = "princeton-nlp/SWE-bench_Verified",
    run_id: str = "eval",
    timeout: int = 3600,
) -> list[EvalResult]:
    """Run SWE-bench evaluation using the official harness.

    predictions: list of {"instance_id": ..., "model_name_or_path": ..., "model_patch": ...}
    """
    results = []

    # Write predictions to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    ) as f:
        for pred in predictions:
            f.write(json.dumps(pred) + "\n")
        pred_path = f.name

    try:
        cmd = [
            "python", "-m", "swebench.harness.run_evaluation",
            "--predictions_path", pred_path,
            "--swe_bench_tasks", dataset_name,
            "--log_level", "INFO",
            "--run_id", run_id,
        ]

        logger.info(f"Running SWE-bench harness: {' '.join(cmd)}")

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if proc.returncode != 0:
            logger.error(f"SWE-bench harness error: {proc.stderr[:2000]}")
            # Return empty results with error
            for pred in predictions:
                results.append(EvalResult(
                    instance_id=pred["instance_id"],
                    agent_name=pred.get("model_name_or_path", "unknown"),
                    error=proc.stderr[:500],
                ))
            return results

        # Parse results from swebench output
        results = _parse_harness_output(predictions, run_id)

    except subprocess.TimeoutExpired:
        logger.error("SWE-bench harness timed out")
        for pred in predictions:
            results.append(EvalResult(
                instance_id=pred["instance_id"],
                agent_name=pred.get("model_name_or_path", "unknown"),
                error="Harness timeout",
            ))
    except FileNotFoundError:
        logger.warning("SWE-bench not installed, using simplified evaluation")
        results = _simplified_evaluation(predictions)

    return results


def _parse_harness_output(
    predictions: list[dict], run_id: str
) -> list[EvalResult]:
    """Parse SWE-bench harness output files."""
    results = []

    # Look for the results in the standard swebench output location
    report_paths = list(Path(".").glob(f"**/report_{run_id}*.json"))

    report_data = {}
    for rp in report_paths:
        try:
            data = json.loads(rp.read_text())
            report_data.update(data)
        except (json.JSONDecodeError, IOError):
            continue

    for pred in predictions:
        iid = pred["instance_id"]
        agent = pred.get("model_name_or_path", "unknown")

        if iid in report_data:
            entry = report_data[iid]
            results.append(EvalResult(
                instance_id=iid,
                agent_name=agent,
                resolved=entry.get("resolved", False),
                fail_to_pass_results=entry.get("tests_status", {}).get(
                    "FAIL_TO_PASS", {}
                ),
                pass_to_pass_results=entry.get("tests_status", {}).get(
                    "PASS_TO_PASS", {}
                ),
            ))
        else:
            results.append(EvalResult(
                instance_id=iid,
                agent_name=agent,
                error="No result from harness",
            ))

    return results


def _simplified_evaluation(predictions: list[dict]) -> list[EvalResult]:
    """Simplified evaluation when SWE-bench harness is not available.
    Just checks if a patch was generated."""
    results = []
    for pred in predictions:
        has_patch = bool(pred.get("model_patch", "").strip())
        results.append(EvalResult(
            instance_id=pred["instance_id"],
            agent_name=pred.get("model_name_or_path", "unknown"),
            resolved=False,  # Can't verify without harness
            error="" if has_patch else "No patch generated",
        ))
    return results


def prepare_predictions(
    agent_results: list[AgentResult], agent_name: str
) -> list[dict]:
    """Convert AgentResults to SWE-bench prediction format."""
    predictions = []
    for result in agent_results:
        predictions.append({
            "instance_id": result.instance_id,
            "model_name_or_path": agent_name,
            "model_patch": result.patch or "",
        })
    return predictions
