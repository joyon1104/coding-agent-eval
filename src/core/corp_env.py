"""Corporate-network mode: proxy / SSL / mirror env-var management.

Activated by the ``--corp`` CLI flag. Reads values from environment variables
(loaded from ``.env`` by ``Config``) and fans them out to:

  1. The agent subprocess on the host (Step 1)
  2. The Docker container (Step 2) — via ``-e`` and ``-v`` on ``docker run``
  3. Language-specific config files inside the container (settings.xml,
     config.toml, .npmrc) — emitted by ``LanguageProfile.pre_test_hook``

Design contract:
  - When ``--corp`` is absent, ``CorpConfig.enabled`` is False and every
    builder returns empty values. Current zero-injection behavior is preserved.
  - Validation runs eagerly so missing variables surface before any task starts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


CONTAINER_CA_PATH = "/etc/ssl/corp-ca.pem"
"""Path the host CA bundle is mounted at inside every container."""


class CorpConfigError(ValueError):
    """Raised when --corp is on but configuration is missing or invalid."""


# ---------------------------------------------------------------------------
# Variable groups read from the environment
# ---------------------------------------------------------------------------

_COMMON_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")
"""Standard proxy variables — passed through as-is to host and container."""

_PYTHON_VARS = ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST")

_GO_VARS = ("GOPROXY", "GOSUMDB", "GOPRIVATE")

_JS_VARS = ("NPM_CONFIG_REGISTRY",)


@dataclass
class CorpConfig:
    """Parsed corporate-network configuration.

    The ``enabled`` flag is the single switch. When False, every accessor
    returns empty values and the harness behaves exactly as before.
    """
    enabled: bool = False
    # Common (proxy + CA)
    proxy: dict[str, str] = field(default_factory=dict)
    ca_bundle_host_path: Path | None = None
    # Python
    python: dict[str, str] = field(default_factory=dict)
    # OS package
    apt_mirror_url: str = ""
    # Multi-tier language registries
    go: dict[str, str] = field(default_factory=dict)
    js: dict[str, str] = field(default_factory=dict)
    maven_mirror_url: str = ""
    gem_source: str = ""
    composer_repo_url: str = ""
    cargo_registry_url: str = ""

    @property
    def has_ca_bundle(self) -> bool:
        return self.ca_bundle_host_path is not None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load(enabled: bool, tier: str = "lite") -> CorpConfig:
    """Read corporate variables from the environment.

    Returns an empty (``enabled=False``) ``CorpConfig`` when the flag is off,
    so callers can unconditionally pass it through without branching.
    The ``tier`` argument doesn't affect parsing — it's used by ``validate``
    to decide which variables are required.
    """
    if not enabled:
        return CorpConfig(enabled=False)

    cfg = CorpConfig(enabled=True)

    # Common
    for v in _COMMON_VARS:
        if os.environ.get(v):
            cfg.proxy[v] = os.environ[v]

    ca_path = os.environ.get("CORP_CA_BUNDLE_PATH", "").strip()
    cfg.ca_bundle_host_path = Path(ca_path) if ca_path else None

    # Python
    for v in _PYTHON_VARS:
        if os.environ.get(v):
            cfg.python[v] = os.environ[v]

    # OS package
    cfg.apt_mirror_url = os.environ.get("CORP_APT_MIRROR_URL", "").strip()

    # Go / JS pass-through
    for v in _GO_VARS:
        if os.environ.get(v):
            cfg.go[v] = os.environ[v]
    for v in _JS_VARS:
        if os.environ.get(v):
            cfg.js[v] = os.environ[v]

    # Single-value language mirrors
    cfg.maven_mirror_url = os.environ.get("CORP_MAVEN_MIRROR_URL", "").strip()
    cfg.gem_source = os.environ.get("CORP_GEM_SOURCE", "").strip()
    cfg.composer_repo_url = os.environ.get("CORP_COMPOSER_REPO_URL", "").strip()
    cfg.cargo_registry_url = os.environ.get("CORP_CARGO_REGISTRY_URL", "").strip()

    return cfg


def validate(cfg: CorpConfig, tier: str = "lite") -> list[str]:
    """Return a list of error strings; empty list means OK.

    Required variables (when ``cfg.enabled``):
      - ``HTTPS_PROXY``
      - ``CORP_CA_BUNDLE_PATH`` (must point to a readable file)
      - ``PIP_INDEX_URL``  (all tiers use Python tooling for at least some
        instances; lite/verified are 100% Python and multi has Python repos)
    Optional variables produce no errors when missing.
    """
    if not cfg.enabled:
        return []

    errors: list[str] = []

    if not cfg.proxy.get("HTTPS_PROXY"):
        errors.append("HTTPS_PROXY (unset)")

    if not cfg.ca_bundle_host_path:
        errors.append("CORP_CA_BUNDLE_PATH (unset)")
    elif not cfg.ca_bundle_host_path.is_file():
        errors.append(
            f"CORP_CA_BUNDLE_PATH (file not found: {cfg.ca_bundle_host_path})"
        )

    if not cfg.python.get("PIP_INDEX_URL"):
        errors.append("PIP_INDEX_URL (unset)")

    return errors


def raise_on_invalid(cfg: CorpConfig, tier: str = "lite") -> None:
    """Validate and raise ``CorpConfigError`` if anything is missing/invalid."""
    errors = validate(cfg, tier)
    if errors:
        bullets = "\n  - ".join(errors)
        raise CorpConfigError(
            "corp mode is on but the following are missing or invalid:\n  - "
            + bullets
        )


# ---------------------------------------------------------------------------
# Builders — host (Step 1) and container (Step 2)
# ---------------------------------------------------------------------------

def _ca_fanout(ca_path_str: str) -> dict[str, str]:
    """Expand a single CA bundle path to all the env vars tools recognize.

    Same dict shape is used for host and container; only the path value
    differs between them.
    """
    return {
        "REQUESTS_CA_BUNDLE": ca_path_str,
        "SSL_CERT_FILE": ca_path_str,
        "CURL_CA_BUNDLE": ca_path_str,
        "GIT_SSL_CAINFO": ca_path_str,
        "NODE_EXTRA_CA_CERTS": ca_path_str,
        "BUNDLE_SSL_CA_CERT": ca_path_str,
        "CARGO_HTTP_CAINFO": ca_path_str,
    }


def build_host_env(cfg: CorpConfig) -> dict[str, str]:
    """Return env-var dict to merge into the agent subprocess env (Step 1).

    Empty dict when corp mode is off — callers can ``env.update(...)`` it
    unconditionally.
    """
    if not cfg.enabled:
        return {}

    env: dict[str, str] = {}
    env.update(cfg.proxy)
    if cfg.ca_bundle_host_path:
        env.update(_ca_fanout(str(cfg.ca_bundle_host_path)))
    env.update(cfg.python)
    env.update(cfg.go)
    env.update(cfg.js)

    # Translate harness-named vars to tool-native ones the host might invoke
    if cfg.gem_source:
        # bundler's mirror-replacement convention
        env["BUNDLE_MIRROR__HTTPS://RUBYGEMS__ORG"] = cfg.gem_source
    if cfg.cargo_registry_url:
        env["CARGO_REGISTRIES_CRATES_IO_PROTOCOL"] = "sparse"

    return env


def build_container_env(cfg: CorpConfig) -> dict[str, str]:
    """Return env vars that should appear inside the container.

    Same shape as ``build_host_env`` but CA paths point at the in-container
    mount path (``CONTAINER_CA_PATH``), not the host path.
    """
    if not cfg.enabled:
        return {}

    env: dict[str, str] = {}
    env.update(cfg.proxy)
    if cfg.ca_bundle_host_path:
        env.update(_ca_fanout(CONTAINER_CA_PATH))
    env.update(cfg.python)
    env.update(cfg.go)
    env.update(cfg.js)

    if cfg.gem_source:
        env["BUNDLE_MIRROR__HTTPS://RUBYGEMS__ORG"] = cfg.gem_source
    if cfg.cargo_registry_url:
        env["CARGO_REGISTRIES_CRATES_IO_PROTOCOL"] = "sparse"

    return env


def build_docker_run_args(cfg: CorpConfig) -> list[str]:
    """Return the ``-e KEY=VAL`` and ``-v HOST:CTR:ro`` flags for docker run.

    Empty list when corp mode is off. Used by ``docker_evaluator`` to extend
    the existing ``docker run`` command.
    """
    if not cfg.enabled:
        return []

    args: list[str] = []
    for k, v in build_container_env(cfg).items():
        args.extend(["-e", f"{k}={v}"])

    if cfg.ca_bundle_host_path:
        args.extend([
            "-v",
            f"{cfg.ca_bundle_host_path}:{CONTAINER_CA_PATH}:ro",
        ])

    return args


def metadata_dict(cfg: CorpConfig) -> dict:
    """Serializable summary recorded in ``metadata.json``.

    Captures which categories are active (truthy) without leaking the actual
    values — so the metadata is shareable without exposing internal URLs/CA
    paths to dashboard viewers.
    """
    if not cfg.enabled:
        return {"corp_mode": False}
    return {
        "corp_mode": True,
        "corp_active_groups": {
            "proxy": bool(cfg.proxy),
            "ca_bundle": cfg.has_ca_bundle,
            "python_mirror": bool(cfg.python),
            "apt_mirror": bool(cfg.apt_mirror_url),
            "go": bool(cfg.go),
            "js": bool(cfg.js),
            "maven": bool(cfg.maven_mirror_url),
            "gem": bool(cfg.gem_source),
            "composer": bool(cfg.composer_repo_url),
            "cargo": bool(cfg.cargo_registry_url),
        },
    }
