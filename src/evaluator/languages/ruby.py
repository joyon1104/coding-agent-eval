"""Ruby language profile — RSpec/Minitest execution."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.evaluator.languages.profile import LanguageProfile, TestOutcome

if TYPE_CHECKING:
    from src.core.models import EvalTask


class RubyProfile(LanguageProfile):
    name = "ruby"

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

        # Detect format: RSpec (path/to/spec.rb[:line]) vs Minitest (Class#method)
        spec_targets = [t for t in test_names if "_spec.rb" in t or "spec/" in t]
        if spec_targets:
            targets = " ".join(f'"{t}"' for t in test_names)
            return f"cd /testbed && bundle exec rspec {targets} --format documentation 2>&1"

        # Minitest: use rake test or ruby -Itest
        return f"cd /testbed && bundle exec rake test 2>&1"

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr
        # RSpec: "X examples, 0 failures" or "X examples, Y failures"
        # Minitest: "X runs, Y assertions, Z failures"
        outcomes: list[TestOutcome] = []

        for test_name in expected:
            passed = _check_ruby_test(output, test_name)
            outcomes.append(TestOutcome(name=test_name, passed=passed))

        return outcomes


def _check_ruby_test(output: str, test_name: str) -> bool:
    method = test_name.split("#")[-1].split("/")[-1].split(":")[-1]

    for line in output.split("\n"):
        if method not in line:
            continue
        low = line.lower()
        if "0 failures" in low or "passed" in low:
            return True
        if "failure" in low or "error" in low:
            return False

    # RSpec summary: "N examples, 0 failures"
    m = re.search(r"(\d+) examples?, (\d+) failures?", output)
    if m and int(m.group(2)) == 0:
        return True

    # Minitest summary: "N runs, N assertions, 0 failures"
    m = re.search(r"(\d+) failures?, (\d+) errors?", output)
    if m and int(m.group(1)) == 0 and int(m.group(2)) == 0:
        return True

    return False
