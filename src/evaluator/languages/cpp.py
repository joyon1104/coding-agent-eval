"""C++ language profile — CMake/ctest/gtest test execution."""

from __future__ import annotations

import logging
import re
import subprocess
from typing import TYPE_CHECKING

from src.evaluator.languages.profile import LanguageProfile, TestOutcome

if TYPE_CHECKING:
    from src.core.models import EvalTask

logger = logging.getLogger("coding-agent-eval")


class CppProfile(LanguageProfile):
    name = "cpp"

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

        # Primary: ctest -R with OR-pattern (works for CMake-based projects)
        # ctest test names match the gtest Suite.TestName format
        pattern = "|".join(re.escape(t) for t in test_names)
        ctest_cmd = (
            f"cd /testbed && "
            f"ctest --test-dir build -R '{pattern}' --output-on-failure 2>&1"
        )

        # Fallback: direct gtest binary (ctest may not know all test names)
        binary = _guess_binary(test_names[0])
        filter_str = ":".join(test_names)
        gtest_cmd = (
            f"cd /testbed && "
            f"./build/bin/{binary} --gtest_filter='{filter_str}' 2>&1"
        )

        # Run ctest first; fall back to gtest binary on ctest failure
        return f"({ctest_cmd}) || ({gtest_cmd})"

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr
        # gtest: "[       OK ] Suite.Test" / "[  FAILED  ] Suite.Test"
        passed_set = set(re.findall(r"\[\s+OK\s+\]\s+(\S+)", output))
        failed_set = set(re.findall(r"\[\s+FAILED\s+\]\s+(\S+)", output))

        outcomes: list[TestOutcome] = []
        for test_name in expected:
            # Strip timing suffix e.g. "Suite.Test (2 ms)" → "Suite.Test"
            bare = test_name.split(" (")[0]
            if bare in passed_set:
                outcomes.append(TestOutcome(name=test_name, passed=True))
            elif bare in failed_set:
                outcomes.append(TestOutcome(name=test_name, passed=False))
            else:
                outcomes.append(TestOutcome(name=test_name, passed=False))

        return outcomes

    def post_patch_hook(self, container_id: str) -> None:
        """Recompile after patch — C++ binaries are not interpreted."""
        logger.info("  C++ post-patch: running cmake --build...")
        try:
            result = subprocess.run(
                ["docker", "exec", container_id, "bash", "-c",
                 "cd /testbed && cmake --build build -j$(nproc) 2>&1"],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                logger.warning(
                    f"  cmake --build failed (rc={result.returncode}): "
                    f"{(result.stdout + result.stderr)[-300:]}"
                )
        except Exception as e:
            logger.warning(f"  cmake --build error: {e}")


def _guess_binary(test_name: str) -> str:
    """Heuristic: 'PrintfTest.Format' → 'printf-test' binary name."""
    suite = test_name.split(".")[0]
    # CamelCase → kebab-case
    kebab = re.sub(r"(?<!^)(?=[A-Z])", "-", suite).lower()
    return kebab
