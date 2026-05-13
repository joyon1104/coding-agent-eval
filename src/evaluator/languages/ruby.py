"""Ruby language profile — RSpec/Minitest execution.

Test name formats encountered across repos:

  fastlane  : "description - ./spec/foo_spec.rb[1:2:3]"     (RSpec path)
  rubocop   : "returns 0 if there are no offenses shown"     (RSpec description)
  faker     : "test_password"                                 (Minitest bare method)
  fluentd   : "test_ENOENT_error_after_setup_watcher"        (Minitest bare method)
  jekyll    : "TestFilters#test_: description"               (Minitest class#method)

The core problem with `bundle exec rake test` is that bundler 2.1.x/2.2.x
`replace_bin_path` is broken for some images, causing method_missing on rake
initialization. `bundle exec ruby -Ilib -Itest file.rb -n method` bypasses
this entirely and runs the specific test directly.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from typing import TYPE_CHECKING

from src.evaluator.languages import corp_setup
from src.evaluator.languages.profile import LanguageProfile, TestOutcome

if TYPE_CHECKING:
    from src.core.corp_env import CorpConfig
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

        first = test_names[0]

        # RSpec with file paths (fastlane: "description - ./spec/foo_spec.rb[1:2:3]")
        if "_spec.rb" in first or "spec/" in first:
            targets = " ".join(f'"{t}"' for t in test_names)
            return f"cd /testbed && bundle exec rspec {targets} --format documentation 2>&1"

        # Minitest ClassName#method_or_description (jekyll: "TestFilters#test_: desc")
        if "#" in first:
            return _minitest_class_method_cmd(test_names, container_id)

        # Bare Minitest method names (faker/fluentd: "test_password")
        if first.startswith("test_"):
            return _minitest_bare_method_cmd(test_names, container_id)

        # Pure RSpec descriptions without file paths (rubocop)
        return "cd /testbed && bundle exec rspec spec/ --format documentation 2>&1"

    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        output = stdout + stderr
        return [
            TestOutcome(name=t, passed=_check_ruby_test(output, t))
            for t in expected
        ]

    def pre_test_hook(self, container_id: str, corp: "CorpConfig | None") -> None:
        corp_setup.write_bundler_config(container_id, corp)


class RubocopProfile(RubyProfile):
    """rubocop has no rake test task — always use rspec spec/."""

    def build_test_command(
        self, test_names: list[str], task: "EvalTask", container_id: str
    ) -> str:
        if not test_names:
            return "echo 'no tests'"
        return "cd /testbed && bundle exec rspec spec/ --format documentation 2>&1"


# ── helpers ──────────────────────────────────────────────────────────────────

def _find_ruby_file(container_id: str, grep_pattern: str) -> str | None:
    """Grep inside the container for a .rb file matching the pattern."""
    try:
        result = subprocess.run(
            ["docker", "exec", container_id, "bash", "-c",
             f"grep -rl {shlex.quote(grep_pattern)} /testbed 2>/dev/null "
             f"| grep -E '\\.rb$' | grep -E '(test|spec)' | head -1"],
            capture_output=True, text=True, timeout=15,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _minitest_bare_method_cmd(test_names: list[str], container_id: str) -> str:
    """Build command for bare Minitest method names (test_password, test_ENOENT_...)."""
    file_methods: dict[str, list[str]] = {}
    for method in test_names:
        f = _find_ruby_file(container_id, f"def {method}")
        if f:
            file_methods.setdefault(f, []).append(method)

    if not file_methods:
        return "cd /testbed && bundle exec rake test 2>&1"

    cmds = []
    for file_path, methods in file_methods.items():
        if len(methods) == 1:
            n_arg = methods[0]
        else:
            n_arg = "/" + "|".join(re.escape(m) for m in methods) + "/"
        cmds.append(
            f"bundle exec ruby -Ilib -Itest {shlex.quote(file_path)} "
            f"-n {shlex.quote(n_arg)} 2>&1"
        )

    return "cd /testbed && " + " ; ".join(cmds)


def _minitest_class_method_cmd(test_names: list[str], container_id: str) -> str:
    """Build command for ClassName#method format (TestFilters#test_: description)."""
    by_class: dict[str, list[str]] = {}
    for t in test_names:
        cls, method_part = t.split("#", 1)
        by_class.setdefault(cls, []).append(method_part)

    cmds = []
    for cls, method_parts in by_class.items():
        file_path = _find_ruby_file(container_id, f"class {cls}")
        if not file_path:
            continue
        if len(method_parts) == 1:
            n_arg = f"/{re.escape(method_parts[0])}/"
        else:
            n_arg = "/" + "|".join(re.escape(mp) for mp in method_parts) + "/"
        cmds.append(
            f"bundle exec ruby -Ilib -Itest {shlex.quote(file_path)} "
            f"-n {shlex.quote(n_arg)} 2>&1"
        )

    if not cmds:
        return "cd /testbed && bundle exec rake test 2>&1"

    return "cd /testbed && " + " ; ".join(cmds)


# ── output parsers ────────────────────────────────────────────────────────────

def _check_ruby_test(output: str, test_name: str) -> bool:
    # RSpec with file path (fastlane: "description - ./spec/file.rb[1:2:3]")
    if " - ./" in test_name and "_spec.rb" in test_name:
        desc = test_name.split(" - ./")[0]
        return _check_rspec_desc(output, desc)

    # Minitest ClassName#method — rely on per-run summary
    if "#" in test_name:
        return _check_minitest_summary(output)

    # Bare Minitest method names — rely on per-run summary
    if test_name.startswith("test_"):
        return _check_minitest_summary(output)

    # Pure RSpec description (rubocop) — check per-line description markers
    return _check_rspec_desc(output, test_name)


def _check_rspec_desc(output: str, desc: str) -> bool:
    """Return True if an RSpec test with `desc` passed in `output`."""
    for line in output.split("\n"):
        stripped = line.strip()
        if desc not in stripped:
            continue
        low = stripped.lower()
        if "(failed" in low:
            return False
        # Bare description line without failure marker = passed
        if stripped == desc or stripped.startswith(desc + " ") or stripped.endswith(" " + desc):
            return True

    # Fallback: "errors occurred outside of examples" = load failure
    if re.search(r"\d+ errors? occurred outside of examples", output):
        return False
    m = re.search(r"(\d+) examples?, (\d+) failures?", output)
    if m and int(m.group(1)) > 0 and int(m.group(2)) == 0:
        return True
    return False


def _check_minitest_summary(output: str) -> bool:
    """Return True if Minitest summary shows N>0 runs/tests with 0 failures/errors.

    Handles both standard Minitest ("N runs, N assertions, 0 failures, 0 errors")
    and minitest-reporters ("N tests, N assertions, 0 failures, 0 errors").
    """
    if re.search(r"\d+ errors? occurred outside of examples", output):
        return False
    # Matches "N runs, ..." or "N tests, ..." (minitest-reporters)
    m = re.search(
        r"(\d+) (?:runs?|tests?), \d+ assertions?, (\d+) failures?, (\d+) errors?",
        output,
    )
    if m and int(m.group(1)) > 0 and int(m.group(2)) == 0 and int(m.group(3)) == 0:
        return True
    return False
