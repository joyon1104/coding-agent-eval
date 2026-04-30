"""C language profile — ctest/make test execution (jq, micropython, redis, valkey)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.evaluator.languages.profile import LanguageProfile, TestOutcome

if TYPE_CHECKING:
    from src.core.models import EvalTask


class CProfile(LanguageProfile):
    name = "c"

    def get_image_name(self, instance_id: str) -> str:
        transformed = instance_id.replace("__", "_1776_")
        return f"docker.io/swebench/sweb.eval.x86_64.{transformed}:latest"

    def shell_prefix(self) -> str:
        return ""

    def build_test_command(
        self, test_names: list[str], task: "EvalTask", container_id: str
    ) -> str:
        if not test_names:
            return "echo 'no tests'"

        pattern = "|".join(re.escape(t) for t in test_names)

        # Try ctest (CMake-based projects like jq), then make test as fallback
        return (
            f"cd /testbed && "
            f"(ctest --test-dir build -R '{pattern}' --output-on-failure 2>&1 "
            f"|| make test 2>&1)"
        )

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr

        # ctest output: "X/Y Test #N: name ......   Passed" / "Failed"
        passed_set = set(re.findall(r"Test\s+#\d+:\s+(\S+).*\bPassed\b", output))
        failed_set = set(re.findall(r"Test\s+#\d+:\s+(\S+).*\bFailed\b", output))

        # Also check gtest-style (some C projects use a gtest wrapper)
        passed_set |= set(re.findall(r"\[\s+OK\s+\]\s+(\S+)", output))
        failed_set |= set(re.findall(r"\[\s+FAILED\s+\]\s+(\S+)", output))

        overall_ok = (
            "All tests passed" in output
            or (
                "FAILED" not in output
                and "failed" not in output.lower()
                and bool(re.search(r"\bPassed\b|\bOK\b", output))
            )
        )

        outcomes: list[TestOutcome] = []
        for test_name in expected:
            if test_name in passed_set:
                outcomes.append(TestOutcome(name=test_name, passed=True))
            elif test_name in failed_set:
                outcomes.append(TestOutcome(name=test_name, passed=False))
            elif overall_ok:
                outcomes.append(TestOutcome(name=test_name, passed=True))
            else:
                outcomes.append(TestOutcome(name=test_name, passed=False))

        return outcomes
