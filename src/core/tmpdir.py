"""Configure TMPDIR from .env so the project's temp scratch space can be
redirected away from /tmp without setting a system-wide environment variable.

When .env contains TMPDIR=/path/to/dir, this module:
  - creates the directory if missing
  - sets os.environ["TMPDIR"]  (inherited by subprocesses: git, docker, etc.)
  - sets tempfile.tempdir       (used by tempfile.mkdtemp / NamedTemporaryFile)

When TMPDIR is absent or the configured path is unusable, falls back to the
system default (/tmp on Linux) — preserving prior behavior bit-for-bit.

Auto-invoked at module import so that simply importing src.core.config (which
imports this module) ensures TMPDIR is applied before any tempfile usage.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

logger = logging.getLogger("coding-agent-eval")

_INITIALIZED = False


def setup_tmpdir() -> str:
    """Apply TMPDIR from .env. Idempotent.

    Returns the resolved tempdir (.env TMPDIR or system default).
    """
    global _INITIALIZED

    if not _INITIALIZED:
        load_dotenv(PROJECT_ROOT / ".env")
        _INITIALIZED = True

    tmpdir = os.environ.get("TMPDIR")
    if not tmpdir:
        return tempfile.gettempdir()

    tmpdir_path = Path(tmpdir).expanduser()
    try:
        tmpdir_path.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        logger.warning(
            f"TMPDIR={tmpdir} could not be created ({e}); "
            f"falling back to system default tempdir."
        )
        os.environ.pop("TMPDIR", None)
        tempfile.tempdir = None
        return tempfile.gettempdir()

    resolved = str(tmpdir_path)
    os.environ["TMPDIR"] = resolved
    tempfile.tempdir = resolved
    return resolved


def get_tmpdir() -> str:
    """Return current tempdir for non-tempfile callers (e.g. glob patterns)."""
    return os.environ.get("TMPDIR") or tempfile.gettempdir()


# Auto-invoke on import — keep this at module bottom so the helpers above are
# defined first.
setup_tmpdir()
