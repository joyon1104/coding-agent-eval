"""Language profile abstraction for multi-language Docker evaluation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.models import EvalTask


@dataclass(frozen=True)
class TestOutcome:
    """Language-agnostic result for a single test."""
    name: str
    passed: bool
    raw_output: str | None = None


class LanguageProfile(ABC):
    """Per-language policy for running SWE-bench Docker evaluation.

    Subclasses encapsulate all language-specific differences:
    image registry, shell environment, test command construction, and output parsing.
    docker_evaluator.py contains zero language-specific logic.
    """

    name: str

    @abstractmethod
    def get_image_name(self, instance_id: str) -> str:
        """Map instance_id → Docker pull path."""

    @abstractmethod
    def shell_prefix(self) -> str:
        """Prefix prepended to every test-runner docker exec command.

        Python: 'source .../conda.sh && conda activate testbed && '
        All others: '' (environment is already on PATH via Docker ENV)
        """

    @abstractmethod
    def build_test_command(
        self, test_names: list[str], task: "EvalTask", container_id: str
    ) -> str:
        """Return a shell command that runs the listed tests inside /testbed.

        container_id is provided so implementations can docker cp staging files
        (e.g. PythonProfile stages targets to /tmp before the command runs).
        """

    @abstractmethod
    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        """Parse runner output into one TestOutcome per expected test name."""

    def post_patch_hook(self, container_id: str) -> None:
        """Called after the agent patch is applied; no-op by default.

        C++ override: cmake --build build -j$(nproc) to recompile.
        """

    def expected_dirty_at_base(self) -> bool:
        """True if the repo git tree is expected to be dirty at base_commit.

        Java/druid override: setup_repo.sh mutates pom.xml before evaluation.
        """
        return False
