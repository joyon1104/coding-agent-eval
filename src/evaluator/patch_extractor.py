"""Extract and validate patches from agent results."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path


def extract_patch_from_diff(diff_text: str) -> str:
    """Clean and normalize a git diff patch."""
    if not diff_text:
        return ""
    # Remove any non-diff content before the first diff header
    lines = diff_text.split("\n")
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("diff --git") or line.startswith("---") or line.startswith("+++"):
            start = i
            break
    return "\n".join(lines[start:]).strip()


def validate_patch(patch: str) -> bool:
    """Check if a patch looks valid."""
    if not patch:
        return False
    return "diff" in patch or "---" in patch or "+++" in patch


def apply_patch(patch: str, repo_path: str) -> bool:
    """Try to apply a patch to the repo. Returns True on success."""
    if not patch:
        return False
    try:
        result = subprocess.run(
            ["git", "apply", "--check", "-"],
            input=patch,
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
