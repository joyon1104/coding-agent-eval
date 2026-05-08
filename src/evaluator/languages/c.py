"""C language profiles — jq, micropython, redis, valkey."""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
from typing import TYPE_CHECKING

from src.evaluator.languages.profile import LanguageProfile, TestOutcome

if TYPE_CHECKING:
    from src.core.models import EvalTask

logger = logging.getLogger("coding-agent-eval")

_SENTINEL_PASS = "__JQSCRIPT_PASS__:"
_SENTINEL_FAIL = "__JQSCRIPT_FAIL__:"


class CProfile(LanguageProfile):
    """Generic CMake/ctest profile — kept as fallback base."""

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
        return (
            f"cd /testbed && "
            f"(ctest --test-dir build -R '{pattern}' --output-on-failure 2>&1 "
            f"|| make test 2>&1)"
        )

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr
        passed_set = set(re.findall(r"Test\s+#\d+:\s+(\S+).*\bPassed\b", output))
        failed_set = set(re.findall(r"Test\s+#\d+:\s+(\S+).*\bFailed\b", output))
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


# ── jqlang/jq ────────────────────────────────────────────────────────────────

class JqProfile(LanguageProfile):
    """jq — cmake build already present in image; shell-script tests + ctest.

    Test name shapes seen in the dataset:
      - "tests/jqtest", "tests/optionaltest", ...  → shell scripts in /testbed
      - "testc", "test_utf8", "testcu", ...         → ctest names (oniguruma)

    Shell scripts are wrapped with sentinel markers so parse_test_output can
    determine pass/fail from the combined stdout without needing exit codes.
    """

    name = "jq"

    def get_image_name(self, instance_id: str) -> str:
        transformed = instance_id.replace("__", "_1776_")
        return f"docker.io/swebench/sweb.eval.x86_64.{transformed}:latest"

    def shell_prefix(self) -> str:
        return ""

    # jq uses autotools (Makefile at /testbed), not cmake.
    # Oniguruma test binaries live in modules/oniguruma/test/ and sample/.
    _ONIG_DIRS = (
        "/testbed/modules/oniguruma/test",
        "/testbed/modules/oniguruma/sample",
    )

    def post_patch_hook(self, container_id: str) -> None:
        logger.info("  jq post-patch: make rebuild...")
        try:
            result = subprocess.run(
                ["docker", "exec", container_id, "bash", "-c",
                 "cd /testbed && make -j$(nproc) 2>&1 | tail -5"],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                logger.warning(
                    f"  make warning (rc={result.returncode}): "
                    f"{(result.stdout + result.stderr)[-300:]}"
                )
        except Exception as e:
            logger.warning(f"  jq make error: {e}")

        # Rebuild oniguruma test/sample binaries — `make` in /testbed deletes them
        # because libtool removes wrapper scripts when libonig is relinked.
        # `make check` recompiles and runs the tests; we ignore failures here since
        # the binaries are built before tests run (failures are caught in the eval step).
        logger.info("  jq post-patch: rebuilding oniguruma test/sample binaries...")
        try:
            subprocess.run(
                ["docker", "exec", container_id, "bash", "-c",
                 "cd /testbed/modules/oniguruma && make check -j$(nproc) 2>&1 | tail -5 ; true"],
                capture_output=True, text=True, timeout=120,
            )
        except Exception as e:
            logger.warning(f"  oniguruma rebuild error: {e}")

    def build_test_command(
        self, test_names: list[str], task: "EvalTask", container_id: str
    ) -> str:
        if not test_names:
            return "echo 'no tests'"

        # "tests/jqtest" → shell script in /testbed;
        # flat names → oniguruma compiled binaries in test/ or sample/
        path_tests = [t for t in test_names if "/" in t]
        flat_tests = [t for t in test_names if "/" not in t]

        cmds: list[str] = []

        for t in path_tests:
            cmds.append(
                f"(cd /testbed && PATH=/testbed:$PATH ./{t} 2>&1 && "
                f"echo '{_SENTINEL_PASS}{t}' || "
                f"echo '{_SENTINEL_FAIL}{t}')"
            )

        for t in flat_tests:
            # Find the compiled oniguruma binary (test/ or sample/), run with sentinel
            dirs = " ".join(self._ONIG_DIRS)
            cmds.append(
                f"(TESTBIN=$(find {dirs} -maxdepth 1 -name {shlex.quote(t)} -type f 2>/dev/null | head -1); "
                f" [ -n \"$TESTBIN\" ] && ($TESTBIN 2>&1 && echo '{_SENTINEL_PASS}{t}' "
                f"   || echo '{_SENTINEL_FAIL}{t}') || echo '{_SENTINEL_FAIL}{t}: not found')"
            )

        return " ; ".join(cmds)

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr

        passed_set: set[str] = set()
        failed_set: set[str] = set()

        for line in output.splitlines():
            if line.startswith(_SENTINEL_PASS):
                passed_set.add(line[len(_SENTINEL_PASS):])
            elif line.startswith(_SENTINEL_FAIL):
                # Strip optional ": reason" suffix added for "not found" cases
                name = line[len(_SENTINEL_FAIL):].split(":")[0]
                failed_set.add(name)

        outcomes: list[TestOutcome] = []
        for test_name in expected:
            if test_name in passed_set:
                outcomes.append(TestOutcome(name=test_name, passed=True))
            elif test_name in failed_set:
                outcomes.append(TestOutcome(name=test_name, passed=False))
            else:
                outcomes.append(TestOutcome(name=test_name, passed=False))
        return outcomes


# ── micropython/micropython ───────────────────────────────────────────────────

class MicropythonProfile(LanguageProfile):
    """micropython — rebuild Unix port, run .py test files with run_tests.py.

    Test name shape: "basics/fun_calldblstar.py" (relative to tests/).
    run_tests.py output: "<file>  pass" or "<file>  FAIL".
    """

    name = "micropython"

    def get_image_name(self, instance_id: str) -> str:
        transformed = instance_id.replace("__", "_1776_")
        return f"docker.io/swebench/sweb.eval.x86_64.{transformed}:latest"

    def shell_prefix(self) -> str:
        return ""

    def post_patch_hook(self, container_id: str) -> None:
        logger.info("  micropython post-patch: rebuilding Unix port...")
        try:
            result = subprocess.run(
                ["docker", "exec", container_id, "bash", "-c",
                 "cd /testbed/ports/unix && make -j$(nproc) 2>&1 | tail -5"],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                logger.warning(
                    f"  make warning (rc={result.returncode}): "
                    f"{(result.stdout + result.stderr)[-300:]}"
                )
        except Exception as e:
            logger.warning(f"  micropython build error: {e}")

    def build_test_command(
        self, test_names: list[str], task: "EvalTask", container_id: str
    ) -> str:
        if not test_names:
            return "echo 'no tests'"

        files = " ".join(shlex.quote(t) for t in test_names)
        return f"cd /testbed/tests && python3 run_tests.py --target unix {files} 2>&1"

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr

        # run_tests.py: "basics/fun_calldblstar.py  pass" or "...  FAIL"
        passed_set = set(re.findall(r"^(\S+\.py)\s+pass\s*$", output, re.MULTILINE))
        failed_set = set(re.findall(r"^(\S+\.py)\s+FAIL\s*$", output, re.MULTILINE))

        outcomes: list[TestOutcome] = []
        for test_name in expected:
            if test_name in passed_set:
                outcomes.append(TestOutcome(name=test_name, passed=True))
            elif test_name in failed_set:
                outcomes.append(TestOutcome(name=test_name, passed=False))
            else:
                outcomes.append(TestOutcome(name=test_name, passed=False))
        return outcomes


# ── redis/redis  +  valkey-io/valkey ─────────────────────────────────────────

def _find_redis_test_file(container_id: str, test_name: str) -> str | None:
    """Locate the TCL file containing test_name by grepping inside the container.

    Returns the path relative to tests/ without the .tcl suffix
    (e.g. "unit/type/stream"), suitable for --single <file>.
    Falls back to None on any failure so the caller can use `make test`.
    """
    # Use the first 5 words as a fixed-string grep to avoid special-char issues.
    words = test_name.split()[:5]
    if not words:
        return None
    pattern = " ".join(words)
    try:
        result = subprocess.run(
            ["docker", "exec", container_id, "bash", "-c",
             f"grep -rl {shlex.quote(pattern)} /testbed/tests/ 2>/dev/null "
             f"| grep '\\.tcl$' | head -1"],
            capture_output=True, text=True, timeout=15,
        )
        path = result.stdout.strip()
        if not path:
            return None
        # /testbed/tests/unit/type/stream.tcl → unit/type/stream
        rel = re.sub(r"^/testbed/tests/", "", path)
        return rel.removesuffix(".tcl")
    except Exception:
        return None


class RedisProfile(LanguageProfile):
    """Redis — recompile with make, run TCL test helper for a specific file.

    Test name shape: human-readable description such as
    "XTRIM with MINID option, big delta from master record".
    These map to test {} blocks in TCL files under tests/.
    The file is discovered via grep so only the relevant TCL file runs,
    keeping wall-clock time under the 600 s timeout.

    Output format:  [ok]: <description> (N ms)
                    [err]: <description> (N ms)
    """

    name = "redis"

    def get_image_name(self, instance_id: str) -> str:
        transformed = instance_id.replace("__", "_1776_")
        return f"docker.io/swebench/sweb.eval.x86_64.{transformed}:latest"

    def shell_prefix(self) -> str:
        return ""

    def post_patch_hook(self, container_id: str) -> None:
        logger.info("  Redis post-patch: recompiling with make...")
        try:
            result = subprocess.run(
                ["docker", "exec", container_id, "bash", "-c",
                 "cd /testbed && make -j$(nproc) 2>&1 | tail -5"],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                logger.warning(
                    f"  make warning (rc={result.returncode}): "
                    f"{(result.stdout + result.stderr)[-300:]}"
                )
        except Exception as e:
            logger.warning(f"  redis make error: {e}")

    def build_test_command(
        self, test_names: list[str], task: "EvalTask", container_id: str
    ) -> str:
        if not test_names:
            return "echo 'no tests'"

        test_file = _find_redis_test_file(container_id, test_names[0])
        if test_file:
            logger.info(f"  Redis test file discovered: {test_file}")
            return f"cd /testbed && tclsh tests/test_helper.tcl --single {test_file} 2>&1"

        # Fallback: full suite (slow, may timeout)
        logger.warning("  Redis: test file discovery failed; falling back to make test")
        return "cd /testbed && make test 2>&1"

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr

        passed_set: set[str] = set()
        failed_set: set[str] = set()

        for line in output.splitlines():
            line = line.strip()
            if line.startswith("[ok]:"):
                # Strip trailing "(N ms)" timing suffix
                name = re.sub(r"\s*\(\d+\s*ms\)\s*$", "", line[5:]).strip()
                passed_set.add(name)
            elif line.startswith("[err]:"):
                name = re.sub(r"\s*\(\d+\s*ms\)\s*$", "", line[6:]).strip()
                failed_set.add(name)

        outcomes: list[TestOutcome] = []
        for test_name in expected:
            if test_name in passed_set:
                outcomes.append(TestOutcome(name=test_name, passed=True))
            elif test_name in failed_set:
                outcomes.append(TestOutcome(name=test_name, passed=False))
            else:
                outcomes.append(TestOutcome(name=test_name, passed=False))
        return outcomes


class ValkeyProfile(RedisProfile):
    """Valkey is a Redis fork — identical test structure and runner."""

    name = "valkey"
