"""Python language profile — 1:1 migration of the existing SWE-bench Python evaluation."""

from __future__ import annotations

import platform
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from src.evaluator.languages.profile import LanguageProfile, TestOutcome

if TYPE_CHECKING:
    from src.core.models import EvalTask

_ARCH = "x86_64" if platform.machine() in ("x86_64", "AMD64") else "arm64"


class PythonProfile(LanguageProfile):
    name = "python"

    def get_image_name(self, instance_id: str) -> str:
        return f"ghcr.io/epoch-research/swe-bench.eval.{_ARCH}.{instance_id}:latest"

    def shell_prefix(self) -> str:
        return (
            "source /opt/miniconda3/etc/profile.d/conda.sh "
            "&& conda activate testbed && "
        )

    def build_test_command(
        self, test_names: list[str], task: "EvalTask", container_id: str
    ) -> str:
        """Stage Django/pytest targets to /tmp files, return the combined runner command."""
        django_targets: list[str] = []
        pytest_targets: list[str] = []

        for t in test_names:
            t = t.strip()
            if not t:
                continue
            if "::" in t or t.endswith(".py"):
                pytest_targets.append(t)
            elif " (" in t and t.endswith(")"):
                method, cls = t.rsplit(" (", 1)
                django_targets.append(f"{cls.rstrip(')')}.{method}")
            elif t.startswith("test_") and "." in t:
                django_targets.append(t)
            # else: unsupported shape (e.g. "#21962 - html escape..."); silently skip

        _stage(container_id, django_targets, "/tmp/django_targets.txt")
        _stage(container_id, pytest_targets, "/tmp/pytest_targets.txt")

        return (
            "cd /testbed && "
            "RC=0 && "
            "if [ -f tests/runtests.py ] && [ -s /tmp/django_targets.txt ]; then "
            "  DJANGO=$(tr '\\n' ' ' < /tmp/django_targets.txt); "
            "  python tests/runtests.py $DJANGO --verbosity 2 2>&1 || RC=$?; "
            "fi; "
            "if [ -s /tmp/pytest_targets.txt ]; then "
            "  PYTEST=$(tr '\\n' ' ' < /tmp/pytest_targets.txt); "
            "  python -m pytest $PYTEST -v --no-header 2>&1 || RC=$?; "
            "fi; "
            "exit $RC"
        )

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        raw = _parse_django_pytest(stdout + stderr, expected)
        return [TestOutcome(name=name, passed=passed) for name, passed in raw.items()]


def _stage(container_id: str, targets: list[str], container_path: str) -> None:
    """Write targets to a temp file and docker cp it into the container."""
    content = ("\n".join(targets) + "\n") if targets else ""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.write(content)
    tmp.close()
    try:
        subprocess.run(
            ["docker", "cp", tmp.name, f"{container_id}:{container_path}"],
            capture_output=True, timeout=10,
        )
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def _parse_django_pytest(output: str, test_names: list[str]) -> dict[str, bool]:
    """Exact replica of the pre-refactor _parse_test_output logic."""
    results: dict[str, bool] = {}

    for test_name in test_names:
        test_name_stripped = test_name.strip()

        if " (" in test_name_stripped and test_name_stripped.endswith(")"):
            method, class_path = test_name_stripped.rsplit(" (", 1)
            class_path = class_path.rstrip(")")
            search_terms = [method, f"{class_path}.{method}", test_name_stripped]
        elif "::" in test_name_stripped:
            search_terms = [test_name_stripped, test_name_stripped.split("::")[-1]]
        else:
            search_terms = [test_name_stripped, test_name_stripped.split(".")[-1]]

        passed = False
        found = False

        for term in search_terms:
            for line in output.split("\n"):
                if term not in line:
                    continue
                found = True
                if "..." in line and (
                    "... ok" in line or " ok" in line.split("...")[-1]
                ):
                    passed = True
                    break
                if "PASSED" in line:
                    passed = True
                    break
                if "FAIL" in line or "ERROR" in line:
                    passed = False
                    break
            if found:
                break

        results[test_name] = passed

    return results
