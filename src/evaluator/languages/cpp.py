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

        filter_str = ":".join(test_names)

        # Iterate over all compiled *-test binaries and run each with --gtest_filter.
        # This avoids guessing which binary contains which test suite:
        #   - ctest -R <gtest-name> fails because ctest registers by binary name
        #     (e.g. "printf-test"), not gtest Suite.Name ("PrintfTest.MinusFlag"),
        #     and returns exit 0 when no tests match, so a plain || fallback never fires.
        #   - Multiple gtest suites (util_test, format_test, ...) can live in a
        #     single binary, making binary-name heuristics unreliable.
        # Binaries that don't contain the target tests produce no matching output.
        return (
            "cd /testbed && "
            "shopt -s nullglob && "
            f"for bin in ./build/bin/*-test ./build/bin/*_test; do "
            f"  [ -x \"$bin\" ] && \"$bin\" --gtest_filter='{filter_str}' 2>&1; "
            "done"
        )

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
