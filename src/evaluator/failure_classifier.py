"""Structured failure classification for evaluation pipeline diagnostics.

Every failure in the pipeline is classified along three axes:
  failure_stage    — where in the pipeline it occurred
  failure_category — the class of problem (model vs environment vs network …)
  root_cause       — machine-readable identifier for the specific failure

These are attached to EvalResult / AgentResult so the final report can
distinguish model capability failures from infrastructure problems.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# failure_stage constants
# ---------------------------------------------------------------------------
STAGE_REPO_CLONE = "repo_clone"
STAGE_SANDBOX_SETUP = "sandbox_setup"
STAGE_AGENT_EXECUTION = "agent_execution"
STAGE_PATCH_EXTRACTION = "patch_extraction"
STAGE_DOCKER_PULL = "docker_pull"
STAGE_CONTAINER_STARTUP = "container_startup"
STAGE_DEPENDENCY_INSTALLATION = "dependency_installation"
STAGE_TEST_EXECUTION = "test_execution"
STAGE_REPORT_GENERATION = "report_generation"

# ---------------------------------------------------------------------------
# failure_category constants
# ---------------------------------------------------------------------------
CAT_MODEL_FAILURE = "model_failure"
CAT_ENVIRONMENT_FAILURE = "environment_failure"
CAT_NETWORK_FAILURE = "network_failure"
CAT_REGISTRY_FAILURE = "registry_failure"
CAT_DOCKER_FAILURE = "docker_failure"
CAT_DEPENDENCY_FAILURE = "dependency_failure"
CAT_TIMEOUT = "timeout"
CAT_CONFIG_ERROR = "configuration_error"
CAT_INTERNAL_ERROR = "internal_error"

# ---------------------------------------------------------------------------
# Pattern lists for recognising specific failure types
# ---------------------------------------------------------------------------
_SSL_PATTERNS = [
    r"ssl.*verif",
    r"certificate.*verif",
    r"CERTIFICATE_VERIFY_FAILED",
    r"ssl\.SSLError",
    r"self[- ]signed certificate",
    r"unable to verify the first certificate",
    r"certificate has expired",
]

_PROXY_PATTERNS = [
    r"407 Proxy Authentication Required",
    r"proxy.*connect",
    r"CONNECT.*tunnel.*failed",
    r"ProxyError",
]

_NETWORK_PATTERNS = [
    r"Name or service not known",
    r"Network is unreachable",
    r"Connection refused",
    r"Connection timed out",
    r"No route to host",
    r"Temporary failure in name resolution",
    r"getaddrinfo.*failed",
    r"nodename nor servname provided",
    r"ECONNRESET",
    r"ECONNREFUSED",
    r"socket\.gaierror",
]

_PIP_FAIL_PATTERNS = [
    r"ERROR: Could not find a version",
    r"ERROR: No matching distribution",
    r"ERROR: pip.s dependency resolver",
    r"pip.*Could not install",
    r"Failed to build",
    r"error: command.*failed with exit code",
    r"pip.*error",
]

_NPM_FAIL_PATTERNS = [
    r"npm ERR!",
    r"npm install.*failed",
    r"npm WARN.*failed",
]

_APT_FAIL_PATTERNS = [
    r"E: Unable to locate package",
    r"E: Package.*has no installation candidate",
    r"apt-get install.*failed",
    r"dpkg.*error",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    """Return True if text matches any compiled pattern (case-insensitive)."""
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


# ---------------------------------------------------------------------------
# Public classifiers — each returns (failure_category, root_cause)
# ---------------------------------------------------------------------------

def classify_pull_failure(error_text: str) -> tuple[str, str]:
    """Classify a Docker image pull failure."""
    if _matches_any(error_text, _SSL_PATTERNS):
        return CAT_NETWORK_FAILURE, "ssl_verification_failed"
    if _matches_any(error_text, _PROXY_PATTERNS):
        return CAT_NETWORK_FAILURE, "proxy_connection_failed"
    if "timeout" in error_text.lower():
        return CAT_TIMEOUT, "docker_image_pull_timeout"
    if any(x in error_text.lower() for x in ("not found", "manifest unknown", "404")):
        return CAT_REGISTRY_FAILURE, "image_not_found"
    if any(x in error_text.lower() for x in ("unauthorized", "denied", "authentication")):
        return CAT_REGISTRY_FAILURE, "registry_auth_failed"
    if _matches_any(error_text, _NETWORK_PATTERNS):
        return CAT_NETWORK_FAILURE, "network_connection_failed"
    return CAT_DOCKER_FAILURE, "docker_pull_failed"


def classify_container_failure(error_text: str) -> tuple[str, str]:
    """Classify a container startup failure."""
    if "no such image" in error_text.lower():
        return CAT_DOCKER_FAILURE, "image_not_found"
    if "permission" in error_text.lower() or "Permission denied" in error_text:
        return CAT_DOCKER_FAILURE, "docker_permission_denied"
    if "out of memory" in error_text.lower() or "OOM" in error_text:
        return CAT_DOCKER_FAILURE, "container_oom"
    return CAT_DOCKER_FAILURE, "container_crashed"


def classify_dependency_failure(error_text: str, exit_code: int = 1) -> tuple[str, str]:
    """Classify a dependency installation failure inside a container."""
    if _matches_any(error_text, _SSL_PATTERNS):
        return CAT_NETWORK_FAILURE, "ssl_verification_failed"
    if _matches_any(error_text, _PROXY_PATTERNS):
        return CAT_NETWORK_FAILURE, "proxy_connection_failed"
    if _matches_any(error_text, _NETWORK_PATTERNS):
        return CAT_NETWORK_FAILURE, "network_connection_failed"
    if _matches_any(error_text, _NPM_FAIL_PATTERNS):
        return CAT_DEPENDENCY_FAILURE, "npm_install_failed"
    if _matches_any(error_text, _PIP_FAIL_PATTERNS):
        return CAT_DEPENDENCY_FAILURE, "pip_install_failed"
    if _matches_any(error_text, _APT_FAIL_PATTERNS):
        return CAT_DEPENDENCY_FAILURE, "apt_install_failed"
    return CAT_DEPENDENCY_FAILURE, "dependency_install_failed"


def classify_test_execution_failure(
    error_text: str, timed_out: bool = False
) -> tuple[str, str]:
    """Classify a test execution failure (environment, not model)."""
    if timed_out:
        return CAT_TIMEOUT, "test_command_timeout"
    if _matches_any(error_text, _SSL_PATTERNS):
        return CAT_NETWORK_FAILURE, "ssl_verification_failed"
    if _matches_any(error_text, _NETWORK_PATTERNS):
        return CAT_NETWORK_FAILURE, "network_connection_failed"
    if "killed" in error_text.lower() or "oom" in error_text.lower():
        return CAT_ENVIRONMENT_FAILURE, "container_oom"
    return CAT_ENVIRONMENT_FAILURE, "test_execution_failed"


def classify_patch_failure(error_text: str) -> tuple[str, str]:
    """Classify a patch extraction or application failure (agent-side)."""
    lower = error_text.lower()
    if "empty" in lower or "no changes" in lower or "git_diff_empty" in lower:
        return CAT_MODEL_FAILURE, "git_diff_empty"
    if "no patch" in lower or "not generated" in lower:
        return CAT_MODEL_FAILURE, "no_patch_generated"
    if "apply" in lower:
        return CAT_MODEL_FAILURE, "patch_apply_failed"
    return CAT_MODEL_FAILURE, "patch_extraction_failed"


def classify_agent_failure(error_text: str) -> tuple[str, str]:
    """Classify a Step-1 agent execution failure."""
    lower = error_text.lower()
    if "timeout" in lower or "timed out" in lower:
        return CAT_TIMEOUT, "agent_execution_timeout"
    if "budget" in lower or "cost limit" in lower:
        return CAT_INTERNAL_ERROR, "budget_exceeded"
    if "disk" in lower or "no space" in lower:
        return CAT_ENVIRONMENT_FAILURE, "disk_space_exhausted"
    if "repo" in lower and ("clone" in lower or "setup" in lower):
        return CAT_ENVIRONMENT_FAILURE, "repo_setup_failed"
    return CAT_INTERNAL_ERROR, "agent_execution_failed"


def classify_sandbox_failure(error_text: str) -> tuple[str, str]:
    """Classify a sandbox/repo-clone failure (Step 1)."""
    lower = error_text.lower()
    if "disk" in lower or "no space" in lower:
        return CAT_ENVIRONMENT_FAILURE, "disk_space_exhausted"
    if _matches_any(error_text, _NETWORK_PATTERNS) or "clone" in lower:
        return CAT_NETWORK_FAILURE, "repo_clone_failed"
    return CAT_ENVIRONMENT_FAILURE, "sandbox_setup_failed"


# ---------------------------------------------------------------------------
# Helper: build structured details dict
# ---------------------------------------------------------------------------

def build_details(
    command: str = "",
    stderr_snippet: str = "",
    stdout_snippet: str = "",
    exit_code: int | None = None,
) -> dict:
    """Build the structured ``details`` dict attached to a failure record."""
    d: dict = {}
    if command:
        d["command"] = command
    if stderr_snippet:
        d["stderr_snippet"] = stderr_snippet[:500]
    if stdout_snippet:
        d["stdout_snippet"] = stdout_snippet[:500]
    if exit_code is not None:
        d["exit_code"] = exit_code
    return d


# ---------------------------------------------------------------------------
# Aggregate helper used by the reporter
# ---------------------------------------------------------------------------

INFRA_CATEGORIES = {
    CAT_ENVIRONMENT_FAILURE,
    CAT_NETWORK_FAILURE,
    CAT_REGISTRY_FAILURE,
    CAT_DOCKER_FAILURE,
    CAT_DEPENDENCY_FAILURE,
    CAT_TIMEOUT,
    CAT_CONFIG_ERROR,
    CAT_INTERNAL_ERROR,
}


def is_infrastructure_failure(failure_category: str) -> bool:
    """Return True if the failure is environmental, not the model's fault."""
    return failure_category in INFRA_CATEGORIES
