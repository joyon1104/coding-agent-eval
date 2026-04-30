"""Java language profile — Maven/Surefire test execution."""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

from src.evaluator.languages.profile import LanguageProfile, TestOutcome

if TYPE_CHECKING:
    from src.core.models import EvalTask


class JavaProfile(LanguageProfile):
    name = "java"

    def get_image_name(self, instance_id: str) -> str:
        # Docker Hub; double-underscore separator becomes _1776_
        transformed = instance_id.replace("__", "_1776_")
        return f"docker.io/swebench/sweb.eval.x86_64.{transformed}:latest"

    def shell_prefix(self) -> str:
        # JDK and Maven are on PATH via Docker ENV — no activation needed
        return ""

    def build_test_command(
        self, test_names: list[str], task: "EvalTask", container_id: str
    ) -> str:
        if not test_names:
            return "echo 'no tests'"

        module = _resolve_maven_module(container_id, test_names[0])
        tests_param = _format_dtest(test_names)

        pl_flag = f"-pl {module} " if module and module != "." else ""
        return (
            f"cd /testbed && "
            f"mvn test {pl_flag}"
            f"-Dtest='{tests_param}' "
            f"-DfailIfNoTests=false 2>&1"
        )

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr
        failed_set = _extract_failed_tests(output)
        build_success = "BUILD SUCCESS" in output

        outcomes: list[TestOutcome] = []
        for test_name in expected:
            method = test_name.split("#")[-1] if "#" in test_name else test_name.split(".")[-1]
            class_name = (
                test_name.split("#")[0].split(".")[-1]
                if "#" in test_name
                else test_name.split(".")[-2]
                if "." in test_name
                else test_name
            )
            short_key = f"{class_name}.{method}"
            if any(short_key in f or method == f.split(".")[-1] for f in failed_set):
                outcomes.append(TestOutcome(name=test_name, passed=False))
            elif build_success:
                outcomes.append(TestOutcome(name=test_name, passed=True))
            else:
                outcomes.append(TestOutcome(name=test_name, passed=False))

        return outcomes

    def expected_dirty_at_base(self) -> bool:
        # druid's setup_repo.sh mutates pom.xml at base commit
        return True


def _format_dtest(test_names: list[str]) -> str:
    """Format Maven -Dtest parameter from FQCN#method list.

    ['com.foo.Bar#testOne', 'com.foo.Bar#testTwo'] → 'Bar#testOne+Bar#testTwo'
    Multiple classes are joined with commas.
    """
    parts: list[str] = []
    for name in test_names:
        if "#" in name:
            fqcn, method = name.rsplit("#", 1)
            class_name = fqcn.split(".")[-1]
            parts.append(f"{class_name}#{method}")
        else:
            parts.append(name.split(".")[-1])
    return "+".join(parts)


def _resolve_maven_module(container_id: str, test_name: str) -> str:
    """Find the Maven submodule containing the test class via docker exec find."""
    fqcn = test_name.split("#")[0] if "#" in test_name else test_name
    class_name = fqcn.split(".")[-1] + ".java"
    try:
        result = subprocess.run(
            ["docker", "exec", container_id, "bash", "-c",
             f"find /testbed -name '{class_name}' -path '*/test/*' 2>/dev/null | head -1"],
            capture_output=True, text=True, timeout=15,
        )
        path = result.stdout.strip()
        if not path:
            return "."
        rel = path.replace("/testbed/", "")
        parts = rel.split("/")
        return parts[0] if len(parts) > 1 else "."
    except Exception:
        return "."


def _extract_failed_tests(output: str) -> set[str]:
    """Extract failed test identifiers from Maven Surefire console output.

    Handles both 'Failed tests:' and 'Tests in error:' sections.
    Returns a set of 'ClassName.methodName' strings.
    """
    failed: set[str] = set()
    in_section = False
    for line in output.split("\n"):
        if re.match(r"Failed tests:|Tests in error:", line):
            in_section = True
            continue
        if in_section:
            stripped = line.strip()
            if not stripped or re.match(r"Tests run:", stripped):
                in_section = False
                continue
            # 'ClassName.methodName' or 'ClassName.methodName: message'
            identifier = stripped.split(":")[0].strip()
            if identifier:
                failed.add(identifier)
    return failed
