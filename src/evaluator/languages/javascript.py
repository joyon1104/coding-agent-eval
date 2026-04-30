"""JavaScript/TypeScript language profile — Jest/Vitest/Mocha test execution."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.evaluator.languages.profile import LanguageProfile, TestOutcome

if TYPE_CHECKING:
    from src.core.models import EvalTask


class JavaScriptProfile(LanguageProfile):
    name = "javascript"

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

        # Test names: "describe block > test name" or bare "test name"
        # Use Jest --testNamePattern (regex) or npm test as fallback
        pattern = "|".join(re.escape(t.split("::")[-1]) for t in test_names)
        return (
            f"cd /testbed && "
            f"(npx jest --testNamePattern '{pattern}' --no-coverage --passWithNoTests 2>&1 "
            f"|| npx vitest run --reporter verbose 2>&1 "
            f"|| npm test 2>&1)"
        )

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr
        outcomes: list[TestOutcome] = []

        for test_name in expected:
            name_part = test_name.split("::")[-1].split("/")[-1]
            passed = _check_js_test(output, name_part)
            outcomes.append(TestOutcome(name=test_name, passed=passed))

        return outcomes


def _check_js_test(output: str, name_part: str) -> bool:
    for line in output.split("\n"):
        if name_part not in line:
            continue
        # Jest/Vitest: "✓ test name" / "✗ test name" / "✕ test name"
        if re.search(r"[✓✔√]\s", line) or "PASS" in line:
            return True
        if re.search(r"[✗✕✘×]\s", line) or "FAIL" in line:
            return False

    # Jest summary: "X passed, Y total"
    m = re.search(r"(\d+) passed", output)
    if m:
        fail_m = re.search(r"(\d+) failed", output)
        if not fail_m:
            return True

    return False
