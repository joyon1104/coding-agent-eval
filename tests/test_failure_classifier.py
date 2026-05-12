"""Tests for src/evaluator/failure_classifier.py."""

import pytest

from src.evaluator.failure_classifier import (
    CAT_CONFIG_ERROR,
    CAT_DEPENDENCY_FAILURE,
    CAT_DOCKER_FAILURE,
    CAT_MODEL_FAILURE,
    CAT_NETWORK_FAILURE,
    CAT_QUOTA_EXCEEDED,
    CAT_REGISTRY_FAILURE,
    CAT_TIMEOUT,
    CAT_ENVIRONMENT_FAILURE,
    CAT_INTERNAL_ERROR,
    STAGE_AGENT_EXECUTION,
    STAGE_REPO_CLONE,
    STAGE_SANDBOX_SETUP,
    build_details,
    classify_agent_failure,
    classify_claude_code_api_error,
    classify_container_failure,
    classify_dependency_failure,
    classify_patch_failure,
    classify_pull_failure,
    classify_sandbox_failure,
    classify_test_execution_failure,
    is_infrastructure_failure,
)


# ---------------------------------------------------------------------------
# classify_pull_failure
# ---------------------------------------------------------------------------

class TestClassifyPullFailure:
    def test_ssl_error(self):
        cat, root = classify_pull_failure(
            "error: Get https://ghcr.io/v2/: x509: certificate verify failed"
        )
        assert cat == CAT_NETWORK_FAILURE
        assert root == "ssl_verification_failed"

    def test_proxy_error(self):
        cat, root = classify_pull_failure(
            "407 Proxy Authentication Required from corporate-proxy"
        )
        assert cat == CAT_NETWORK_FAILURE
        assert root == "proxy_connection_failed"

    def test_timeout(self):
        cat, root = classify_pull_failure("hard timeout after 600s")
        assert cat == CAT_TIMEOUT
        assert root == "docker_image_pull_timeout"

    def test_image_not_found(self):
        cat, root = classify_pull_failure(
            "Error response from daemon: manifest unknown: not found"
        )
        assert cat == CAT_REGISTRY_FAILURE
        assert root == "image_not_found"

    def test_auth_error(self):
        cat, root = classify_pull_failure(
            "Error response from daemon: unauthorized: authentication required"
        )
        assert cat == CAT_REGISTRY_FAILURE
        assert root == "registry_auth_failed"

    def test_network_connection_refused(self):
        cat, root = classify_pull_failure("Connection refused to registry.example.com")
        assert cat == CAT_NETWORK_FAILURE
        assert root == "network_connection_failed"

    def test_generic_docker_failure(self):
        cat, root = classify_pull_failure("unexpected error during pull")
        assert cat == CAT_DOCKER_FAILURE
        assert root == "docker_pull_failed"


# ---------------------------------------------------------------------------
# classify_container_failure
# ---------------------------------------------------------------------------

class TestClassifyContainerFailure:
    def test_image_not_found(self):
        cat, root = classify_container_failure(
            "Error: No such image: ghcr.io/epoch-research/foo:latest"
        )
        assert cat == CAT_DOCKER_FAILURE
        assert root == "image_not_found"

    def test_permission_denied(self):
        cat, root = classify_container_failure(
            "docker: Got permission denied while trying to connect to the Docker daemon socket"
        )
        assert cat == CAT_DOCKER_FAILURE
        assert root == "docker_permission_denied"

    def test_oom(self):
        cat, root = classify_container_failure("container killed: out of memory")
        assert cat == CAT_DOCKER_FAILURE
        assert root == "container_oom"

    def test_generic_crash(self):
        cat, root = classify_container_failure("container exited with code 137")
        assert cat == CAT_DOCKER_FAILURE
        assert root == "container_crashed"


# ---------------------------------------------------------------------------
# classify_dependency_failure
# ---------------------------------------------------------------------------

class TestClassifyDependencyFailure:
    def test_ssl_inside_pip(self):
        cat, root = classify_dependency_failure(
            "SSL: CERTIFICATE_VERIFY_FAILED when installing requests"
        )
        assert cat == CAT_NETWORK_FAILURE
        assert root == "ssl_verification_failed"

    def test_npm_failure(self):
        cat, root = classify_dependency_failure("npm ERR! code ENOTFOUND\nnpm ERR! network")
        assert cat == CAT_DEPENDENCY_FAILURE
        assert root == "npm_install_failed"

    def test_pip_failure(self):
        cat, root = classify_dependency_failure(
            "ERROR: Could not find a version that satisfies the requirement foo==1.2.3"
        )
        assert cat == CAT_DEPENDENCY_FAILURE
        assert root == "pip_install_failed"

    def test_apt_failure(self):
        cat, root = classify_dependency_failure(
            "E: Unable to locate package libssl-dev-custom"
        )
        assert cat == CAT_DEPENDENCY_FAILURE
        assert root == "apt_install_failed"

    def test_proxy_in_pip(self):
        cat, root = classify_dependency_failure(
            "ProxyError: HTTPSConnectionPool(host='pypi.org'): 407 Proxy Authentication Required"
        )
        assert cat == CAT_NETWORK_FAILURE
        assert root == "proxy_connection_failed"

    def test_generic_install_failure(self):
        cat, root = classify_dependency_failure("make: *** [install] Error 1", exit_code=2)
        assert cat == CAT_DEPENDENCY_FAILURE
        assert root == "dependency_install_failed"


# ---------------------------------------------------------------------------
# classify_test_execution_failure
# ---------------------------------------------------------------------------

class TestClassifyTestExecutionFailure:
    def test_timeout(self):
        cat, root = classify_test_execution_failure("", timed_out=True)
        assert cat == CAT_TIMEOUT
        assert root == "test_command_timeout"

    def test_ssl(self):
        cat, root = classify_test_execution_failure("ssl.SSLError: certificate verify failed")
        assert cat == CAT_NETWORK_FAILURE
        assert root == "ssl_verification_failed"

    def test_oom(self):
        cat, root = classify_test_execution_failure("process killed OOM")
        assert cat == CAT_ENVIRONMENT_FAILURE
        assert root == "container_oom"

    def test_generic(self):
        cat, root = classify_test_execution_failure("bash: command not found: pytest")
        assert cat == CAT_ENVIRONMENT_FAILURE
        assert root == "test_execution_failed"


# ---------------------------------------------------------------------------
# classify_patch_failure
# ---------------------------------------------------------------------------

class TestClassifyPatchFailure:
    def test_empty_diff(self):
        cat, root = classify_patch_failure("git_diff_empty: no changes after agent run")
        assert cat == CAT_MODEL_FAILURE
        assert root == "git_diff_empty"

    def test_no_patch_generated(self):
        cat, root = classify_patch_failure("no patch generated by agent")
        assert cat == CAT_MODEL_FAILURE
        assert root == "no_patch_generated"

    def test_apply_failed(self):
        cat, root = classify_patch_failure("git apply failed at line 42")
        assert cat == CAT_MODEL_FAILURE
        assert root == "patch_apply_failed"

    def test_generic(self):
        cat, root = classify_patch_failure("unknown patch problem")
        assert cat == CAT_MODEL_FAILURE
        assert root == "patch_extraction_failed"


# ---------------------------------------------------------------------------
# classify_agent_failure
# ---------------------------------------------------------------------------

class TestClassifyAgentFailure:
    def test_timeout(self):
        cat, root = classify_agent_failure("agent execution timed out after 1800s")
        assert cat == CAT_TIMEOUT
        assert root == "agent_execution_timeout"

    def test_budget(self):
        cat, root = classify_agent_failure("cost limit exceeded: budget $5.00")
        assert cat == CAT_INTERNAL_ERROR
        assert root == "budget_exceeded"

    def test_disk(self):
        cat, root = classify_agent_failure("no space left on disk for sandbox")
        assert cat == CAT_ENVIRONMENT_FAILURE
        assert root == "disk_space_exhausted"

    def test_generic(self):
        cat, root = classify_agent_failure("subprocess crashed unexpectedly")
        assert cat == CAT_INTERNAL_ERROR
        assert root == "agent_execution_failed"


# ---------------------------------------------------------------------------
# classify_sandbox_failure
# ---------------------------------------------------------------------------

class TestClassifySandboxFailure:
    def test_disk(self):
        cat, root = classify_sandbox_failure("DiskSpaceError: no space left")
        assert cat == CAT_ENVIRONMENT_FAILURE
        assert root == "disk_space_exhausted"

    def test_clone_network(self):
        cat, root = classify_sandbox_failure("fatal: unable to clone: Connection refused")
        assert cat == CAT_NETWORK_FAILURE
        assert root == "repo_clone_failed"

    def test_generic(self):
        cat, root = classify_sandbox_failure("something went wrong with sandbox")
        assert cat == CAT_ENVIRONMENT_FAILURE
        assert root == "sandbox_setup_failed"


# ---------------------------------------------------------------------------
# build_details
# ---------------------------------------------------------------------------

class TestBuildDetails:
    def test_full(self):
        d = build_details(
            command="pip install foo",
            stderr_snippet="ERROR: not found",
            stdout_snippet="Collecting foo",
            exit_code=1,
        )
        assert d["command"] == "pip install foo"
        assert d["stderr_snippet"] == "ERROR: not found"
        assert d["stdout_snippet"] == "Collecting foo"
        assert d["exit_code"] == 1

    def test_truncation(self):
        long_text = "x" * 600
        d = build_details(stderr_snippet=long_text)
        assert len(d["stderr_snippet"]) == 500

    def test_empty_fields_omitted(self):
        d = build_details(command="cmd")
        assert "stderr_snippet" not in d
        assert "stdout_snippet" not in d
        assert "exit_code" not in d


# ---------------------------------------------------------------------------
# classify_claude_code_api_error
# ---------------------------------------------------------------------------

class TestClassifyClaudeCodeApiError:
    def test_429_rate_limit(self):
        cat, root = classify_claude_code_api_error(429, "You've hit your limit · resets 1:20pm")
        assert cat == CAT_QUOTA_EXCEEDED
        assert root == "rate_limit_exceeded"

    def test_529_overloaded(self):
        cat, root = classify_claude_code_api_error(529, "service overloaded")
        assert cat == CAT_TIMEOUT
        assert root == "api_overloaded"

    def test_500_server_error(self):
        cat, root = classify_claude_code_api_error(500, "internal server error")
        assert cat == CAT_INTERNAL_ERROR
        assert root == "api_server_error"

    def test_401_auth(self):
        cat, root = classify_claude_code_api_error(401, "unauthorized")
        assert cat == CAT_CONFIG_ERROR
        assert root == "api_auth_failed"

    def test_text_only_quota(self):
        # No HTTP code provided — fall back to text matching
        cat, root = classify_claude_code_api_error(None, "You've hit your limit")
        assert cat == CAT_QUOTA_EXCEEDED
        assert root == "rate_limit_exceeded"

    def test_text_only_overloaded(self):
        cat, root = classify_claude_code_api_error(None, "Anthropic API overloaded")
        assert cat == CAT_TIMEOUT
        assert root == "api_overloaded"

    def test_unknown(self):
        cat, root = classify_claude_code_api_error(None, "weird API error")
        assert cat == CAT_INTERNAL_ERROR
        assert root == "api_error"


# ---------------------------------------------------------------------------
# is_infrastructure_failure
# ---------------------------------------------------------------------------

class TestIsInfrastructureFailure:
    def test_model_failure_is_not_infra(self):
        assert not is_infrastructure_failure(CAT_MODEL_FAILURE)

    @pytest.mark.parametrize("cat", [
        CAT_ENVIRONMENT_FAILURE,
        CAT_NETWORK_FAILURE,
        CAT_REGISTRY_FAILURE,
        CAT_DOCKER_FAILURE,
        CAT_DEPENDENCY_FAILURE,
        CAT_TIMEOUT,
        CAT_QUOTA_EXCEEDED,
        CAT_INTERNAL_ERROR,
    ])
    def test_infra_categories(self, cat):
        assert is_infrastructure_failure(cat)

    def test_empty_string(self):
        assert not is_infrastructure_failure("")
