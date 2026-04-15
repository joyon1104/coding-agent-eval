"""Docker sandbox with disk management."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.core.config import Config


class DiskSpaceError(Exception):
    pass


class DiskAwareSandbox:
    """Manages Docker containers for SWE-bench evaluation."""

    def __init__(self, config: Config):
        self.config = config
        self.min_free_gb = 3.0
        self.clean_after = config.env_config.get("docker", {}).get(
            "clean_after_run", False
        )
        self.memory_limit = config.env_config.get("docker", {}).get(
            "memory_limit", "8g"
        )
        self.cpu_limit = config.env_config.get("docker", {}).get("cpu_limit", 4)
        self._workdirs: dict[str, Path] = {}

    def check_disk(self):
        free_gb = shutil.disk_usage(os.getcwd()).free / (1024 ** 3)
        if free_gb < self.min_free_gb:
            self._cleanup_old_images()
            free_gb = shutil.disk_usage(os.getcwd()).free / (1024 ** 3)
            if free_gb < self.min_free_gb:
                raise DiskSpaceError(
                    f"Disk space too low: {free_gb:.1f}GB (need {self.min_free_gb}GB)"
                )

    def setup_repo(self, instance_id: str, repo: str, base_commit: str) -> str:
        """Clone repo and checkout base commit. Returns repo path."""
        workdir = Path(tempfile.mkdtemp(prefix=f"cape_{instance_id}_"))
        self._workdirs[instance_id] = workdir

        repo_url = f"https://github.com/{repo}.git"
        repo_path = workdir / "repo"

        # Full clone to ensure base_commit is reachable
        result = subprocess.run(
            ["git", "clone", repo_url, str(repo_path)],
            capture_output=True, timeout=600, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr[:500]}")

        result = subprocess.run(
            ["git", "checkout", base_commit],
            cwd=str(repo_path),
            capture_output=True, timeout=60, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git checkout {base_commit} failed: {result.stderr[:500]}")

        return str(repo_path)

    def cleanup(self, instance_id: str):
        """Clean up working directory for an instance."""
        workdir = self._workdirs.pop(instance_id, None)
        if workdir and workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)

    def cleanup_all(self):
        for iid in list(self._workdirs.keys()):
            self.cleanup(iid)

    def _cleanup_old_images(self):
        """Remove dangling Docker images to free space."""
        try:
            subprocess.run(
                ["docker", "image", "prune", "-f"],
                capture_output=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
