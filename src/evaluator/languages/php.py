"""PHP language profile — PHPUnit test execution."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.evaluator.languages.profile import LanguageProfile, TestOutcome

if TYPE_CHECKING:
    from src.core.models import EvalTask


class PhpProfile(LanguageProfile):
    name = "php"

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

        # PHPUnit: --filter accepts a regex matching test method names
        methods = [t.split("::")[-1] for t in test_names]
        filter_pattern = "|".join(re.escape(m) for m in methods)
        return (
            f"cd /testbed && "
            f"./vendor/bin/phpunit --filter '{filter_pattern}' --testdox 2>&1"
        )

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr

        # PHPUnit testdox: " [x] Method name" (pass) or " [ ] Method name" (fail)
        passed_labels = set(re.findall(r"\[x\]\s+(.+)", output))
        failed_labels = set(re.findall(r"\[ \]\s+(.+)", output))
        overall_ok = bool(re.search(r"OK \(\d+ tests?", output))

        outcomes: list[TestOutcome] = []
        for test_name in expected:
            method = test_name.split("::")[-1]
            # testdox converts camelCase to "Title case words"; do a loose match
            if any(method.lower() in label.lower() for label in passed_labels):
                outcomes.append(TestOutcome(name=test_name, passed=True))
            elif any(method.lower() in label.lower() for label in failed_labels):
                outcomes.append(TestOutcome(name=test_name, passed=False))
            elif overall_ok:
                outcomes.append(TestOutcome(name=test_name, passed=True))
            else:
                outcomes.append(TestOutcome(name=test_name, passed=False))

        return outcomes
