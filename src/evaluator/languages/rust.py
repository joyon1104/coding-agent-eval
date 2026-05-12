"""Rust language profile — cargo test execution."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.evaluator.languages import corp_setup
from src.evaluator.languages.profile import LanguageProfile, TestOutcome

if TYPE_CHECKING:
    from src.core.corp_env import CorpConfig
    from src.core.models import EvalTask


class RustProfile(LanguageProfile):
    name = "rust"

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
        # cargo test accepts a filter; run each individually for clarity
        # test names: "module::test_name" or just "test_name"
        funcs = [t.split("::")[-1] for t in test_names]
        pattern = "|".join(re.escape(f) for f in funcs)
        return f"cd /testbed && cargo test '{pattern}' -- --nocapture 2>&1"

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr
        outcomes: list[TestOutcome] = []

        for test_name in expected:
            func_name = test_name.split("::")[-1]
            passed = False
            for line in output.split("\n"):
                if func_name not in line:
                    continue
                if re.search(r"\.\.\.\s*ok\b", line):
                    passed = True
                    break
                if re.search(r"\.\.\.\s*FAILED\b", line):
                    passed = False
                    break
            outcomes.append(TestOutcome(name=test_name, passed=passed))

        return outcomes

    def pre_test_hook(self, container_id: str, corp: "CorpConfig | None") -> None:
        corp_setup.write_cargo_config(container_id, corp)
