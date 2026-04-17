"""Docker sandbox with disk management and repo caching."""

from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from src.core.config import Config, PROJECT_ROOT

logger = logging.getLogger("coding-agent-eval")

# Shared repo cache directory
REPO_CACHE_DIR = PROJECT_ROOT / ".repo_cache"


class DiskSpaceError(Exception):
    pass


class DiskAwareSandbox:
    """Manages repo cloning with cache and disk management."""

    def __init__(self, config: Config):
        self.config = config
        self.min_free_gb = 3.0
        self.clean_after = config.env_config.get("docker", {}).get(
            "clean_after_run", False
        )
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

    def setup_repo(
        self, instance_id: str, repo: str, base_commit: str,
        max_retries: int = 3, retry_delay: int = 10,
    ) -> str:
        """Clone repo and checkout base commit. Returns repo path.

        Uses a shared cache: the repo is cloned once into .repo_cache/,
        then copied to a working directory per task. This avoids
        re-cloning large repos (e.g. django ~500MB) for every task.
        """
        workdir = Path(tempfile.mkdtemp(prefix=f"cape_{instance_id}_"))
        self._workdirs[instance_id] = workdir
        repo_path = workdir / "repo"

        # Get or create cached bare repo
        cached = self._get_cached_repo(repo, max_retries, retry_delay)

        # Copy from cache and checkout
        logger.info(f"  Copying cached repo for {instance_id}...")
        result = subprocess.run(
            ["git", "clone", "--local", str(cached), str(repo_path)],
            capture_output=True, timeout=120, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Local clone from cache failed: {result.stderr.strip()}")

        result = subprocess.run(
            ["git", "checkout", "-f", base_commit],
            cwd=str(repo_path),
            capture_output=True, timeout=60, text=True,
        )
        if result.returncode != 0:
            # Commit might be newer than cache — update cache and retry
            logger.info(f"  Commit not found, updating cache...")
            self._update_cached_repo(cached)
            # Re-copy
            shutil.rmtree(repo_path, ignore_errors=True)
            subprocess.run(
                ["git", "clone", "--local", str(cached), str(repo_path)],
                capture_output=True, timeout=120, text=True,
            )
            result = subprocess.run(
                ["git", "checkout", "-f", base_commit],
                cwd=str(repo_path),
                capture_output=True, timeout=60, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git checkout {base_commit} failed: {result.stderr.strip()}")

        return str(repo_path)

    def _get_cached_repo(self, repo: str, max_retries: int, retry_delay: int) -> Path:
        """Get path to cached repo. Clones if not exists."""
        REPO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # e.g. "django/django" -> ".repo_cache/django__django"
        cache_name = repo.replace("/", "__")
        cached_path = REPO_CACHE_DIR / cache_name

        if cached_path.exists():
            logger.info(f"  Using cached repo: {cached_path}")
            return cached_path

        # Clone with retries
        repo_url = f"https://github.com/{repo}.git"
        last_error = ""

        for attempt in range(1, max_retries + 1):
            if cached_path.exists():
                shutil.rmtree(cached_path, ignore_errors=True)

            logger.info(f"  Caching repo (attempt {attempt}/{max_retries}): {repo}")
            result = subprocess.run(
                ["git", "clone", "--bare", repo_url, str(cached_path)],
                capture_output=True, timeout=600, text=True,
            )

            if result.returncode == 0:
                logger.info(f"  Repo cached: {cached_path}")
                return cached_path

            last_error = result.stderr.strip()
            logger.warning(f"  Cache clone failed (attempt {attempt}): {last_error}")
            if attempt < max_retries:
                delay = retry_delay * attempt
                logger.info(f"  Retrying in {delay}s...")
                time.sleep(delay)

        raise RuntimeError(f"Failed to cache repo after {max_retries} attempts: {last_error}")

    def _update_cached_repo(self, cached_path: Path):
        """Fetch latest changes into cached repo."""
        subprocess.run(
            ["git", "fetch", "--all"],
            cwd=str(cached_path),
            capture_output=True, timeout=600, text=True,
        )

    def cleanup(self, instance_id: str):
        """Clean up working directory for an instance."""
        workdir = self._workdirs.pop(instance_id, None)
        if workdir and workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)

    def cleanup_all(self):
        for iid in list(self._workdirs.keys()):
            self.cleanup(iid)

    def cleanup_stale_workdirs(self):
        """Remove orphaned /tmp/cape_* directories from previous runs."""
        count = 0
        for d in glob.glob("/tmp/cape_*"):
            p = Path(d)
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                count += 1
        if count:
            logger.info(f"  Cleaned up {count} stale temp directories")

    def _cleanup_old_images(self):
        """Remove dangling Docker images to free space."""
        try:
            subprocess.run(
                ["docker", "image", "prune", "-f"],
                capture_output=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    @staticmethod
    def cleanup_docker_resources():
        """Remove stopped containers and dangling images."""
        try:
            result = subprocess.run(
                ["docker", "container", "prune", "-f"],
                capture_output=True, text=True, timeout=30,
            )
            if "Deleted" in result.stdout:
                logger.info(f"  Docker container cleanup: {result.stdout.strip()}")

            result = subprocess.run(
                ["docker", "image", "prune", "-f"],
                capture_output=True, text=True, timeout=60,
            )
            if "Total reclaimed" in result.stdout:
                logger.info(f"  Docker image cleanup: {result.stdout.strip()}")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
