"""Go language profile — go test execution."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.evaluator.languages.profile import LanguageProfile, TestOutcome

if TYPE_CHECKING:
    from src.core.models import EvalTask


class GoProfile(LanguageProfile):
    name = "go"

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
        # Test names are like "TestFuncName" or "pkg.TestFuncName"
        funcs = [t.split(".")[-1] for t in test_names]
        pattern = "|".join(re.escape(f) for f in funcs)
        return f"cd /testbed && go test ./... -run '{pattern}' -v 2>&1"

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr
        passed_set = set(re.findall(r"--- PASS:\s+(\S+)", output))
        failed_set = set(re.findall(r"--- FAIL:\s+(\S+)", output))

        outcomes: list[TestOutcome] = []
        for test_name in expected:
            func_name = test_name.split(".")[-1]
            if func_name in passed_set:
                outcomes.append(TestOutcome(name=test_name, passed=True))
            elif func_name in failed_set:
                outcomes.append(TestOutcome(name=test_name, passed=False))
            else:
                outcomes.append(TestOutcome(name=test_name, passed=False))

        return outcomes
