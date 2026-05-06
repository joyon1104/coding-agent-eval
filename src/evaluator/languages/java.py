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

        # `mvn test` requires surefire-junit47 which is absent from the image's
        # local Maven cache and Maven Central is unreachable (network blocked +
        # SSL intercept). Use JUnit's console runner directly instead: the image
        # has junit-4.x in ~/.m2 and all production/test classes are pre-compiled.
        module = _resolve_maven_module(container_id, test_names[0])
        pl_flag = f"-pl {module}" if module and module != "." else ""
        cp_file = f"/tmp/_cp_{module or 'root'}.txt"
        if module and module != ".":
            test_cp = f"/testbed/{module}/target/test-classes:/testbed/{module}/target/classes"
        else:
            test_cp = "/testbed/target/test-classes:/testbed/target/classes"

        # Group by FQCN so each class is run as one JUnitCore invocation
        by_class: dict[str, list[str]] = {}
        for name in test_names:
            cls = name.split("#")[0] if "#" in name else name.rsplit(".", 1)[0]
            by_class.setdefault(cls, []).append(name)

        run_cmds = " ; ".join(
            f"java -cp \"{test_cp}:$(cat {cp_file})\" org.junit.runner.JUnitCore {cls} 2>&1"
            for cls in by_class
        )
        return (
            f"cd /testbed && "
            f"mvn dependency:build-classpath {pl_flag} -o -q "
            f"-Dmdep.outputFile={cp_file} 2>/dev/null && "
            f"{run_cmds}"
        )

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr

        # JUnit console runner failure format: "1) methodName(fully.qualified.ClassName)"
        failed_set: set[str] = set()
        for m in re.finditer(r"^\d+\)\s+(\w+)\(([^)]+)\)", output, re.MULTILINE):
            method, cls = m.group(1), m.group(2)
            failed_set.add(f"{cls}#{method}")

        tests_ran = bool(re.search(r"Tests run:|OK \(", output))

        outcomes: list[TestOutcome] = []
        for test_name in expected:
            cls = test_name.split("#")[0] if "#" in test_name else ""
            method = test_name.split("#")[-1] if "#" in test_name else test_name.split(".")[-1]
            full_key = f"{cls}#{method}"
            if full_key in failed_set:
                outcomes.append(TestOutcome(name=test_name, passed=False))
            elif tests_ran:
                outcomes.append(TestOutcome(name=test_name, passed=True))
            else:
                outcomes.append(TestOutcome(name=test_name, passed=False))

        return outcomes

    def expected_dirty_at_base(self) -> bool:
        # druid's setup_repo.sh mutates pom.xml at base commit
        return True


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
