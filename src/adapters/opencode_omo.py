"""OpenCode + oh-my-opencode adapter (opencode-omo)."""

from __future__ import annotations

from src.adapters.opencode import OpenCodeAdapter


class OpenCodeOMOAdapter(OpenCodeAdapter):
    """Runs OpenCode with oh-my-opencode's ``ulw`` workflow prefix.

    CLI difference vs OpenCodeAdapter:
      OpenCodeAdapter:    opencode run --pure "<task>"
      OpenCodeOMOAdapter: opencode run "ulw <task>"

    --pure is intentionally omitted here so that oh-my-opencode remains active.
    All JSON event parsing, token/cost extraction, failure classification, and
    telemetry logic is inherited unchanged from OpenCodeAdapter.
    """

    name = "opencode-omo"

    def _build_cmd(self, problem_statement: str, repo_path: str) -> list[str]:
        cmd = [
            "opencode", "run", f"ulw {problem_statement}",
            "--format", "json",
            "--dir", repo_path,
            "--dangerously-skip-permissions",
        ]
        model = self.config.get("model")
        if model:
            cmd.extend(["--model", model])
        return cmd
