"""Shared helpers for writing corp-mode config files inside containers.

Languages whose tooling doesn't honor env vars need a config file written
to a well-known path. This module owns the file templates + the
``docker exec`` calls that drop them in place.

All helpers no-op silently if the corresponding ``CorpConfig`` value is empty
so calling them from any ``pre_test_hook`` is always safe.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.corp_env import CorpConfig

logger = logging.getLogger("coding-agent-eval")


def _exec(container_id: str, cmd: str, timeout: int = 30) -> int:
    """Run a shell command inside the container; log on failure."""
    result = subprocess.run(
        ["docker", "exec", container_id, "bash", "-c", cmd],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        logger.warning(
            f"  [corp_setup] command failed (rc={result.returncode}): {cmd[:120]} "
            f"-- {(result.stderr or result.stdout)[:200].strip()}"
        )
    return result.returncode


def _write_file(container_id: str, path: str, content: str) -> None:
    """Write ``content`` to ``path`` inside the container, creating parent dirs."""
    parent = path.rsplit("/", 1)[0] or "/"
    _exec(container_id, f"mkdir -p {shlex.quote(parent)}")
    # tee -a is reliable for arbitrary content; heredocs in docker exec are
    # fragile because of quote escaping rules.
    quoted = shlex.quote(content)
    _exec(container_id, f"printf '%s' {quoted} > {shlex.quote(path)}")


# ---------------------------------------------------------------------------
# Generic: rewrite apt sources (Debian/Ubuntu)
# ---------------------------------------------------------------------------

def rewrite_apt_sources(container_id: str, corp: "CorpConfig | None") -> None:
    """Point apt at the corporate mirror.

    Replaces ``archive.ubuntu.com`` and ``security.ubuntu.com`` host parts in
    every line of ``/etc/apt/sources.list`` (and conf.d files). Idempotent.
    """
    if corp is None or not corp.enabled or not corp.apt_mirror_url:
        return

    # CORP_APT_MIRROR_URL may be like 'http://internal-mirror.corp/ubuntu'.
    # We treat it as a host+prefix replacement for the standard Ubuntu mirrors.
    new_prefix = corp.apt_mirror_url.rstrip("/")
    # Strip scheme so the sed line works regardless of what the source.list uses
    new_host = new_prefix.split("//", 1)[-1]

    sed_cmd = (
        "sed -i.bak "
        f"-e 's|http://archive\\.ubuntu\\.com/ubuntu|{new_prefix}|g' "
        f"-e 's|http://security\\.ubuntu\\.com/ubuntu|{new_prefix}|g' "
        f"-e 's|https://archive\\.ubuntu\\.com/ubuntu|{new_prefix}|g' "
        "/etc/apt/sources.list 2>/dev/null || true"
    )
    _exec(container_id, sed_cmd)
    logger.info(f"  [corp_setup] apt sources rewritten to {new_host}")


# ---------------------------------------------------------------------------
# Java: ~/.m2/settings.xml
# ---------------------------------------------------------------------------

_MAVEN_SETTINGS_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">
  <mirrors>
    <mirror>
      <id>corp-mirror</id>
      <name>Corporate Maven Mirror</name>
      <url>{url}</url>
      <mirrorOf>*</mirrorOf>
    </mirror>
  </mirrors>
</settings>
"""


def write_maven_settings(container_id: str, corp: "CorpConfig | None") -> None:
    if corp is None or not corp.enabled or not corp.maven_mirror_url:
        return
    content = _MAVEN_SETTINGS_TEMPLATE.format(url=corp.maven_mirror_url)
    _write_file(container_id, "/root/.m2/settings.xml", content)
    logger.info("  [corp_setup] maven settings.xml written")


# ---------------------------------------------------------------------------
# Rust: ~/.cargo/config.toml
# ---------------------------------------------------------------------------

_CARGO_CONFIG_TEMPLATE = """\
[source.crates-io]
replace-with = "corp-mirror"

[source.corp-mirror]
registry = "{url}"

[http]
cainfo = "/etc/ssl/corp-ca.pem"
"""


def write_cargo_config(container_id: str, corp: "CorpConfig | None") -> None:
    if corp is None or not corp.enabled or not corp.cargo_registry_url:
        return
    content = _CARGO_CONFIG_TEMPLATE.format(url=corp.cargo_registry_url)
    _write_file(container_id, "/root/.cargo/config.toml", content)
    logger.info("  [corp_setup] cargo config.toml written")


# ---------------------------------------------------------------------------
# PHP: composer config (CLI-based)
# ---------------------------------------------------------------------------

def configure_composer(container_id: str, corp: "CorpConfig | None") -> None:
    if corp is None or not corp.enabled or not corp.composer_repo_url:
        return
    # Composer doesn't honor env for repository config; the CLI form is the
    # most reliable cross-version approach.
    _exec(
        container_id,
        f"composer config -g repo.packagist composer {shlex.quote(corp.composer_repo_url)}",
        timeout=60,
    )
    logger.info("  [corp_setup] composer repo configured")


# ---------------------------------------------------------------------------
# Ruby: bundler global config (~/.bundle/config via CLI)
# ---------------------------------------------------------------------------

def write_bundler_config(container_id: str, corp: "CorpConfig | None") -> None:
    """Configure bundler gem mirror and (optionally) CA cert inside the container.

    Uses ``bundle config --global`` rather than writing the YAML config file
    directly, because the CLI form is version-independent and handles quoting.
    ``BUNDLE_MIRROR__*`` env-var support was added in bundler 2.x and is not
    reliable on all SWE-bench Ruby images.
    """
    if corp is None or not corp.enabled or not corp.gem_source:
        return
    _exec(
        container_id,
        f"bundle config --global mirror.https://rubygems.org {shlex.quote(corp.gem_source)}",
        timeout=30,
    )
    # Point bundler at the mounted corp CA so TLS to the internal mirror works.
    if corp.ca_bundle_host_path:
        _exec(
            container_id,
            "bundle config --global ssl_ca_cert /etc/ssl/corp-ca.pem",
            timeout=30,
        )
    logger.info("  [corp_setup] bundler config written")
