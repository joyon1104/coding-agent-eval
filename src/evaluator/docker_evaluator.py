"""Docker-based test evaluator using official SWE-bench Docker images.

Supports both Python (GHCR) and multi-language (Docker Hub) SWE-bench instances.
Language-specific logic is fully encapsulated in LanguageProfile subclasses
(src/evaluator/languages/). This module contains zero language-specific code.

Workflow per instance:
  1. Pull the SWE-bench Docker image for the instance
  2. Start container (repo + deps already ready)
  3. Apply agent's patch
  4. Reset test-file paths; apply test_patch
  5. Call profile.post_patch_hook() (C++ recompile; no-op for others)
  6. Run FAIL_TO_PASS tests → should pass if fix is correct
  7. Run PASS_TO_PASS tests → should still pass (regression check)

Failure diagnostics:
  Every returned EvalResult carries failure_stage / failure_category / root_cause
  / details so the report can distinguish model failures from infra failures.
  When diagnostics_dir is passed to evaluate_single() / evaluate_batch(), a
  per-task subdirectory is written with structured log artifacts.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import subprocess
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

from src.core.corp_env import CorpConfig, build_docker_run_args
from src.core.models import AgentResult, EvalTask, TaskStatus
from src.evaluator.failure_classifier import (
    CAT_INTERNAL_ERROR,
    CAT_MODEL_FAILURE,
    STAGE_AGENT_EXECUTION,
    STAGE_DOCKER_PULL,
    STAGE_CONTAINER_STARTUP,
    STAGE_DEPENDENCY_INSTALLATION,
    STAGE_TEST_EXECUTION,
    STAGE_PATCH_EXTRACTION,
    build_details,
    classify_container_failure,
    classify_dependency_failure,
    classify_patch_failure,
    classify_pull_failure,
    classify_test_execution_failure,
)
from src.evaluator.languages import corp_setup
from src.evaluator.languages.dispatch import get_profile
from src.evaluator.languages.profile import LanguageProfile
from src.evaluator.registry_utils import RETRYABLE, backoff_seconds, classify
from src.evaluator.swebench_harness import EvalResult

logger = logging.getLogger("coding-agent-eval")

_ARCH = "x86_64" if platform.machine() in ("x86_64", "AMD64") else "arm64"


def get_image_name(instance_id: str, tier: str = "lite") -> str:
    """Return the Docker image URL for an instance.

    Backward-compatible: callers that only pass instance_id get the Python
    (GHCR) image, which is correct for Lite/Verified/full tiers.
    Pass tier="multi" to get the Docker Hub image with __→_1776_ transform.
    """
    if tier == "multi":
        transformed = instance_id.replace("__", "_1776_")
        return f"docker.io/swebench/sweb.eval.x86_64.{transformed}:latest"
    return f"ghcr.io/epoch-research/swe-bench.eval.{_ARCH}.{instance_id}:latest"


def image_exists_locally(image_name: str) -> bool:
    """Check if a Docker image exists locally."""
    result = subprocess.run(
        ["docker", "images", "-q", image_name],
        capture_output=True, text=True, timeout=10,
    )
    return bool(result.stdout.strip())


def pull_image(
    instance_id: str,
    max_retries: int = 3,
    timeout_per_try: int = 600,
    tier: str = "lite",
) -> bool:
    """Pull the SWE-bench Docker image for an instance with retry on transient failures.

    Transient categories (rate_limit / timeout / network) trigger retry with
    exponential backoff, since docker reuses already-downloaded blobs on retry,
    making partial-progress failures cheap to recover from. Persistent failures
    (not_found / auth / tls / dns) bail out immediately.
    """
    image = get_image_name(instance_id, tier)

    if image_exists_locally(image):
        logger.info(f"  Image already exists: {image}")
        return True

    for attempt in range(max_retries + 1):
        logger.info(f"  Pulling image (attempt {attempt + 1}/{max_retries + 1}): {image}")
        try:
            result = subprocess.run(
                ["docker", "pull", image],
                capture_output=True, text=True, timeout=timeout_per_try,
            )
            if result.returncode == 0:
                logger.info(f"  Pull complete: {image}")
                return True
            err = (result.stderr or result.stdout).strip()
            category = classify(err)
        except subprocess.TimeoutExpired:
            err = f"hard timeout after {timeout_per_try}s"
            category = "timeout"

        logger.warning(f"  Pull failed [{category}]: {err[:300]}")

        if category not in RETRYABLE or attempt == max_retries:
            logger.error(f"  Giving up after {attempt + 1} attempts: {image}")
            return False

        delay = backoff_seconds(category, attempt)
        logger.info(f"  Retrying in {delay:.1f}s...")
        time.sleep(delay)

    return False


def _paths_in_patch(patch_text: str) -> list[str]:
    """Extract destination ('b/') file paths from a unified diff."""
    paths = []
    for line in patch_text.split("\n"):
        if line.startswith("diff --git a/"):
            parts = line.split(" b/", 1)
            if len(parts) == 2:
                paths.append(parts[1])
    return paths


def _format_apply_failure(result: subprocess.CompletedProcess, patch: str) -> str:
    """Build an informative error message for a failed `git apply`.

    Includes stderr/stdout excerpt and, if git reports a specific line number
    ("at line N" / "patch failed: file:N"), a small window of the patch
    content around that line so post-hoc debugging can see why it broke
    without needing to re-pull the original patch from disk.
    """
    detail = (result.stderr.strip() or result.stdout.strip() or "(no output)")[:500]
    line_nums: set[int] = set()
    for m in re.finditer(r"at line (\d+)", detail):
        line_nums.add(int(m.group(1)))
    for m in re.finditer(r"patch failed:[^:\n]+:(\d+)", detail):
        # NOTE: this is a line in the target FILE, not the patch — we skip it
        # to avoid confusing noise. Only true "at line N" (patch-internal) wins.
        pass
    if not line_nums:
        return detail

    lines = patch.split("\n")
    ctx_chunks = []
    for n in sorted(line_nums):
        lo = max(1, n - 2)
        hi = min(len(lines), n + 2)
        window = [f"{i:>4}: {lines[i - 1]}" for i in range(lo, hi + 1)]
        ctx_chunks.append(f"patch around line {n}:\n" + "\n".join(window))
    return f"{detail}\n---\n" + "\n---\n".join(ctx_chunks)


def _docker_exec(
    container_id: str, cmd: str, timeout: int = 300, prefix: str = ""
) -> subprocess.CompletedProcess:
    """Execute a command inside the container.

    prefix is prepended to cmd when provided (e.g. conda activate for Python).
    Git operations pass no prefix; only test-runner calls pass profile.shell_prefix().
    """
    full_cmd = f"{prefix}{cmd}" if prefix else cmd
    return subprocess.run(
        ["docker", "exec", container_id, "bash", "-c", full_cmd],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )


def _check_container_environment(container_id: str) -> dict:
    """Quick sanity check of the container environment before running tests.

    Verifies that basic tools and the testbed directory are accessible.
    Returns a dict suitable for writing to environment_check.json.
    """
    checks: dict = {
        "container_id": container_id,
        "timestamp": datetime.now().isoformat(),
        "checks": {},
    }

    probe_commands = [
        ("git", "git --version 2>&1"),
        ("testbed_accessible", "ls /testbed 2>&1"),
        ("python", "python --version 2>&1 || python3 --version 2>&1"),
        ("pip", "pip --version 2>&1 || pip3 --version 2>&1"),
    ]

    all_ok = True
    for name, cmd in probe_commands:
        try:
            r = _docker_exec(container_id, cmd, timeout=15)
            ok = r.returncode == 0
            checks["checks"][name] = {
                "available": ok,
                "output": (r.stdout or r.stderr or "").strip()[:120],
            }
            if not ok:
                all_ok = False
        except subprocess.TimeoutExpired:
            checks["checks"][name] = {"available": False, "output": "timeout"}
            all_ok = False

    checks["all_ok"] = all_ok
    return checks


def _run_tests_in_container(
    container_id: str,
    test_names: list[str],
    profile: LanguageProfile,
    task: EvalTask,
    timeout: int = 300,
) -> tuple[dict[str, bool], str, str]:
    """Run tests inside the SWE-bench container via the language profile.

    Returns (outcomes, stdout, stderr). stdout and stderr are returned
    separately so callers can save them as diagnostic artifacts.

    profile.build_test_command() constructs the shell command (and may stage
    input files via docker cp). profile.parse_test_output() maps runner output
    to pass/fail per test name. All language-specific logic lives in the profile.
    """
    if not test_names:
        return {}, "", ""

    cmd = profile.build_test_command(test_names, task, container_id)
    raw_stdout = ""
    raw_stderr = ""
    timed_out = False

    try:
        result = _docker_exec(
            container_id, cmd, timeout=timeout, prefix=profile.shell_prefix()
        )
        raw_stdout = result.stdout or ""
        raw_stderr = result.stderr or ""
        output = raw_stdout + raw_stderr
        logger.info(f"  Test exit code: {result.returncode}")
        # On non-zero exit, the test runner itself failed (collection error,
        # syntax error, import error, segfault, …) — show the full output so
        # the root cause is visible in run.log, not just in artifacts.
        if result.returncode != 0:
            logger.error(
                f"  Test runner exited non-zero ({result.returncode}). "
                f"Dumping full output:"
            )
            if raw_stderr.strip():
                logger.error(f"  TEST STDERR (full):\n{raw_stderr}")
            if raw_stdout.strip():
                logger.error(f"  TEST STDOUT (full):\n{raw_stdout}")
    except subprocess.TimeoutExpired as e:
        # Salvage whatever the runner wrote before we killed it — tests that
        # completed before the timeout still have valid results in the buffer.
        raw_stdout = (e.stdout or b"").decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        raw_stderr = (e.stderr or b"").decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        output = raw_stdout + raw_stderr
        timed_out = True
        logger.warning(
            f"  Test execution timed out after {timeout}s; parsing {len(output)} "
            f"chars of partial output (completed tests keep real results; rest are False)"
        )
        if raw_stderr.strip():
            logger.error(f"  TEST STDERR (partial, full):\n{raw_stderr}")
        if raw_stdout.strip():
            logger.error(f"  TEST STDOUT (partial, full):\n{raw_stdout}")

    outcomes = profile.parse_test_output(output, "", test_names)
    passed = sum(1 for o in outcomes if o.passed)
    failed_names = [o.name for o in outcomes if not o.passed]
    logger.info(f"  Test results: {passed}/{len(outcomes)} passed")
    if failed_names:
        # Truncate when there are very many (e.g. Django P2P with hundreds)
        # but log every name when the count is manageable.
        if len(failed_names) <= 30:
            for n in failed_names:
                logger.warning(f"    FAIL: {n}")
        else:
            for n in failed_names[:30]:
                logger.warning(f"    FAIL: {n}")
            logger.warning(f"    ... and {len(failed_names) - 30} more failed")
    return {o.name: o.passed for o in outcomes}, raw_stdout, raw_stderr


def _write_patch_file(patch_text: str) -> str:
    """Write patch to a temp file, ensuring trailing newline."""
    if not patch_text.endswith("\n"):
        patch_text += "\n"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False)
    f.write(patch_text)
    f.close()
    return f.name


def _save_task_diagnostics(
    task_diag_dir: Path,
    *,
    docker_setup_log: str = "",
    env_check: dict | None = None,
    test_stdout: str = "",
    test_stderr: str = "",
    failure_summary: dict | None = None,
) -> None:
    """Persist per-task diagnostic artifacts under task_diag_dir."""
    task_diag_dir.mkdir(parents=True, exist_ok=True)

    if docker_setup_log:
        (task_diag_dir / "docker_setup.log").write_text(
            docker_setup_log, encoding="utf-8"
        )
    if env_check is not None:
        (task_diag_dir / "environment_check.json").write_text(
            json.dumps(env_check, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    if test_stdout:
        (task_diag_dir / "test_stdout.log").write_text(
            test_stdout, encoding="utf-8"
        )
    if test_stderr:
        (task_diag_dir / "test_stderr.log").write_text(
            test_stderr, encoding="utf-8"
        )
    if failure_summary is not None:
        (task_diag_dir / "failure_summary.json").write_text(
            json.dumps(failure_summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def evaluate_single(
    task: EvalTask,
    agent_result: AgentResult,
    timeout: int = 600,
    f2p_timeout: int | None = None,
    p2p_timeout: int | None = None,
    tier: str = "lite",
    diagnostics_dir: Path | None = None,
    corp_config: CorpConfig | None = None,
) -> EvalResult:
    """Evaluate a single agent result using the SWE-bench Docker image.

    `timeout` is the default per-phase budget. `f2p_timeout` / `p2p_timeout`
    override it for the respective test batches — P2P can hold hundreds of
    tests on Django instances, so it gets a more generous default (1.5x).
    `tier` selects the language profile: "lite"/"verified"/"full" → Python;
    "multi" → dispatch by repo.

    `diagnostics_dir`: if provided, per-task subdirectory is created with
    docker_setup.log, environment_check.json, test_stdout.log, test_stderr.log,
    and (on failure) failure_summary.json.

    `corp_config`: when ``enabled``, ``docker run`` is extended with ``-e``
    proxy/mirror/CA vars and ``-v`` for the host CA bundle, and
    ``profile.pre_test_hook`` writes language-specific config files. A None
    or disabled value preserves current behavior bit-for-bit.
    """
    f2p_timeout = f2p_timeout if f2p_timeout is not None else timeout
    p2p_timeout = p2p_timeout if p2p_timeout is not None else int(timeout * 1.5)

    task_diag_dir = (
        diagnostics_dir / task.instance_id if diagnostics_dir is not None else None
    )

    profile = get_profile(task, tier)

    # --- Accumulators for docker_setup.log ---
    setup_log_parts: list[str] = []

    def _log(msg: str) -> None:
        logger.info(msg)
        setup_log_parts.append(msg)

    def _flush_diag(
        *,
        env_check: dict | None = None,
        test_stdout: str = "",
        test_stderr: str = "",
        failure_summary: dict | None = None,
    ) -> None:
        if task_diag_dir is None:
            return
        _save_task_diagnostics(
            task_diag_dir,
            docker_setup_log="\n".join(setup_log_parts),
            env_check=env_check,
            test_stdout=test_stdout,
            test_stderr=test_stderr,
            failure_summary=failure_summary,
        )

    # --- No patch ---
    # ``root_cause`` is always ``no_patch_generated`` (the observable symptom),
    # but ``failure_stage`` / ``failure_category`` reflect the *underlying*
    # cause from Step 1. If the agent subprocess errored (rate limit, timeout,
    # crash …) those should NOT be misattributed to ``model_failure``.
    # Only when Step 1 finished SUCCESS with an empty patch is it a genuine
    # model failure (the model returned without making any edits).
    if not agent_result.patch:
        if agent_result.status == TaskStatus.ERROR:
            step1_stage = agent_result.failure_stage or STAGE_AGENT_EXECUTION
            step1_cat = agent_result.failure_category or CAT_INTERNAL_ERROR
            step1_err = agent_result.error_message or "No patch generated"
        else:
            step1_stage = STAGE_PATCH_EXTRACTION
            step1_cat = CAT_MODEL_FAILURE
            step1_err = "No patch generated"

        result = EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
            resolved=False,
            eval_status="fail",
            error=step1_err,
            failure_stage=step1_stage,
            failure_category=step1_cat,
            root_cause="no_patch_generated",
            details=dict(agent_result.failure_details or {}),
        )
        _flush_diag(
            failure_summary={
                "status": "FAILED",
                "failure_stage": result.failure_stage,
                "failure_category": result.failure_category,
                "root_cause": result.root_cause,
                "error": result.error,
                "step1_status": agent_result.status.value if hasattr(agent_result.status, "value") else str(agent_result.status),
            }
        )
        return result

    image = profile.get_image_name(task.instance_id)

    # --- 1. Pull image ---
    _log(f"[docker_pull] Pulling image: {image}")
    if not pull_image(task.instance_id, tier=tier):
        pull_err = f"Failed to pull image: {image}"
        cat, root = classify_pull_failure(pull_err)
        result = EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
            eval_status="error",
            error=pull_err,
            failure_stage=STAGE_DOCKER_PULL,
            failure_category=cat,
            root_cause=root,
            details=build_details(
                command=f"docker pull {image}",
                stderr_snippet=pull_err,
            ),
        )
        _flush_diag(
            failure_summary={
                "status": "FAILED",
                "failure_stage": result.failure_stage,
                "failure_category": result.failure_category,
                "root_cause": result.root_cause,
                "details": result.details,
            }
        )
        return result

    _log(f"[docker_pull] Image ready: {image}")

    container_name = f"cae_{task.instance_id}"
    container_id = None
    env_check: dict | None = None

    try:
        # --- 2. Start container ---
        _log(f"[container_startup] Starting container from {image}")

        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True, timeout=10,
        )

        # Corp-mode injects -e/-v flags here; empty list when corp is off so
        # the docker run command is bit-for-bit identical to before.
        corp_run_args = build_docker_run_args(corp_config) if corp_config else []
        run_cmd = (
            ["docker", "run", "-d", "--name", container_name]
            + corp_run_args
            + [image, "tail", "-f", "/dev/null"]
        )
        result_proc = subprocess.run(
            run_cmd, capture_output=True, text=True, timeout=30,
        )
        if result_proc.returncode != 0:
            stderr_snippet = result_proc.stderr[:400]
            cat, root = classify_container_failure(stderr_snippet)
            result = EvalResult(
                instance_id=task.instance_id,
                agent_name=agent_result.agent_name,
                eval_status="error",
                error=f"Container start failed: {stderr_snippet}",
                failure_stage=STAGE_CONTAINER_STARTUP,
                failure_category=cat,
                root_cause=root,
                details=build_details(
                    command=f"docker run -d --name {container_name} {image}",
                    stderr_snippet=stderr_snippet,
                    exit_code=result_proc.returncode,
                ),
            )
            _flush_diag(
                failure_summary={
                    "status": "FAILED",
                    "failure_stage": result.failure_stage,
                    "failure_category": result.failure_category,
                    "root_cause": result.root_cause,
                    "details": result.details,
                }
            )
            return result

        container_id = result_proc.stdout.strip()[:12]
        _log(f"[container_startup] Container started: {container_id}")

        # --- Environment check (before touching the repo) ---
        env_check = _check_container_environment(container_id)
        _log(f"[env_check] all_ok={env_check.get('all_ok', False)}")
        if not env_check.get("all_ok", True):
            for check_name, check_data in env_check.get("checks", {}).items():
                if isinstance(check_data, dict) and not check_data.get("available", True):
                    _log(
                        f"[env_check] FAIL: {check_name} "
                        f"(output: {str(check_data.get('output', ''))[:300]})"
                    )

        # --- Corp-mode bootstrap (no-op when corp is off) ---
        # apt mirror is universal; language config files are profile-specific.
        if corp_config is not None and corp_config.enabled:
            corp_setup.rewrite_apt_sources(container_name, corp_config)
            try:
                profile.pre_test_hook(container_name, corp_config)
            except Exception as e:
                _log(f"[corp_setup] pre_test_hook warning: {e}")

        # Detect dependency-level issues from env check (e.g. pip broken)
        if not env_check.get("all_ok", True):
            # Testbed inaccessible = container/image problem, not model
            testbed_ok = env_check["checks"].get("testbed_accessible", {}).get("available", True)
            if not testbed_ok:
                env_output = str(env_check["checks"].get("testbed_accessible", {}).get("output", ""))
                cat, root = classify_dependency_failure(env_output)
                result = EvalResult(
                    instance_id=task.instance_id,
                    agent_name=agent_result.agent_name,
                    eval_status="error",
                    error=f"Container environment check failed: /testbed not accessible",
                    failure_stage=STAGE_DEPENDENCY_INSTALLATION,
                    failure_category=cat,
                    root_cause=root,
                    details=build_details(
                        command="ls /testbed",
                        stderr_snippet=env_output,
                    ),
                )
                _flush_diag(
                    env_check=env_check,
                    failure_summary={
                        "status": "FAILED",
                        "failure_stage": result.failure_stage,
                        "failure_category": result.failure_category,
                        "root_cause": result.root_cause,
                        "details": result.details,
                    },
                )
                return result

        # --- 3. Apply agent's patch ---
        patch_path = _write_patch_file(agent_result.patch)
        subprocess.run(
            ["docker", "cp", patch_path, f"{container_name}:/tmp/agent.patch"],
            capture_output=True, timeout=10,
        )

        result_proc = _docker_exec(
            container_name,
            "cd /testbed && git apply --verbose /tmp/agent.patch",
            timeout=30,
        )
        if result_proc.returncode != 0:
            _log(f"  git apply failed, retrying with --3way...")
            result_proc = _docker_exec(
                container_name,
                "cd /testbed && git apply --verbose --3way /tmp/agent.patch",
                timeout=30,
            )
        if result_proc.returncode != 0:
            _log(f"  --3way failed, retrying with --ignore-whitespace...")
            result_proc = _docker_exec(
                container_name,
                "cd /testbed && git apply --verbose --3way --ignore-whitespace /tmp/agent.patch",
                timeout=30,
            )

        Path(patch_path).unlink(missing_ok=True)

        if result_proc.returncode != 0:
            detail_text = _format_apply_failure(result_proc, agent_result.patch)
            cat, root = classify_patch_failure(detail_text)
            logger.error(
                f"  Agent patch apply FAILED after all fallbacks "
                f"(strict / --3way / --ignore-whitespace). exit={result_proc.returncode}"
            )
            if result_proc.stderr:
                logger.error(f"  git apply STDERR (full):\n{result_proc.stderr}")
            if result_proc.stdout:
                logger.error(f"  git apply STDOUT (full):\n{result_proc.stdout}")
            result = EvalResult(
                instance_id=task.instance_id,
                agent_name=agent_result.agent_name,
                resolved=False,
                eval_status="fail",
                error=f"Agent patch apply failed: {detail_text}",
                failure_stage=STAGE_PATCH_EXTRACTION,
                failure_category=cat,
                root_cause=root,
                details=build_details(
                    command="git apply /tmp/agent.patch",
                    stderr_snippet=(result_proc.stderr or "")[:400],
                    exit_code=result_proc.returncode,
                ),
            )
            _flush_diag(
                env_check=env_check,
                failure_summary={
                    "status": "FAILED",
                    "failure_stage": result.failure_stage,
                    "failure_category": result.failure_category,
                    "root_cause": result.root_cause,
                    "details": result.details,
                },
            )
            return result

        _log(f"  Patch applied successfully")

        # --- 4. Reset test-file paths before test_patch ---
        if task.test_patch:
            test_paths = _paths_in_patch(task.test_patch)
            if test_paths:
                tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
                tmp.write("\n".join(test_paths) + "\n")
                tmp.close()
                try:
                    subprocess.run(
                        ["docker", "cp", tmp.name,
                         f"{container_name}:/tmp/test_paths.txt"],
                        capture_output=True, timeout=10,
                    )
                finally:
                    Path(tmp.name).unlink(missing_ok=True)
                _docker_exec(
                    container_name,
                    "cd /testbed && xargs -d '\\n' git checkout HEAD -- "
                    "< /tmp/test_paths.txt 2>&1 || true",
                    timeout=30,
                )
                _log(f"  Reset {len(test_paths)} test path(s) before test_patch")

        # --- 5. Apply test_patch ---
        if task.test_patch:
            patch_path = _write_patch_file(task.test_patch)
            subprocess.run(
                ["docker", "cp", patch_path,
                 f"{container_name}:/tmp/test.patch"],
                capture_output=True, timeout=10,
            )
            result_proc = _docker_exec(
                container_name,
                "cd /testbed && git apply --verbose /tmp/test.patch",
                timeout=30,
            )
            if result_proc.returncode != 0:
                _log("  test_patch apply failed, retrying with --3way...")
                result_proc = _docker_exec(
                    container_name,
                    "cd /testbed && git apply --verbose --3way /tmp/test.patch",
                    timeout=30,
                )
            Path(patch_path).unlink(missing_ok=True)
            if result_proc.returncode != 0:
                logger.error(
                    f"  test_patch apply FAILED after fallbacks. "
                    f"exit={result_proc.returncode}"
                )
                if result_proc.stderr:
                    logger.error(f"  test_patch STDERR (full):\n{result_proc.stderr}")
                if result_proc.stdout:
                    logger.error(f"  test_patch STDOUT (full):\n{result_proc.stdout}")

        # --- 6. Post-patch hook ---
        profile.post_patch_hook(container_name)

        # --- 7. Run FAIL_TO_PASS tests ---
        _log(f"  Running FAIL_TO_PASS tests ({len(task.FAIL_TO_PASS)})...")
        f2p_results, f2p_stdout, f2p_stderr = _run_tests_in_container(
            container_name, task.FAIL_TO_PASS, profile, task, timeout=f2p_timeout,
        )

        # --- 8. Run PASS_TO_PASS tests ---
        _log(f"  Running PASS_TO_PASS tests ({len(task.PASS_TO_PASS)})...")
        p2p_results, p2p_stdout, p2p_stderr = _run_tests_in_container(
            container_name, task.PASS_TO_PASS, profile, task, timeout=p2p_timeout,
        )

        # Combine test output for artifact saving
        combined_stdout = f2p_stdout + p2p_stdout
        combined_stderr = f2p_stderr + p2p_stderr

        all_f2p_pass = all(f2p_results.values()) if f2p_results else False
        all_p2p_pass = (all(p2p_results.values()) if p2p_results else True)
        resolved = all_f2p_pass and all_p2p_pass

        error_note = ""
        if not task.FAIL_TO_PASS:
            error_note = "no FAIL_TO_PASS tests in dataset row; resolution unverifiable"
            resolved = False

        _flush_diag(env_check=env_check, test_stdout=combined_stdout, test_stderr=combined_stderr)

        return EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
            resolved=resolved,
            eval_status="success",
            fail_to_pass_results=f2p_results,
            pass_to_pass_results=p2p_results,
            error=error_note,
        )

    except subprocess.TimeoutExpired:
        # Wall-clock exceeded — environmental, not agent fault.
        cat, root = classify_test_execution_failure("timeout", timed_out=True)
        result = EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
            eval_status="error",
            error=f"Timeout after {timeout}s",
            failure_stage=STAGE_TEST_EXECUTION,
            failure_category=cat,
            root_cause=root,
            details=build_details(stderr_snippet=f"Timeout after {timeout}s"),
        )
        _flush_diag(
            env_check=env_check,
            failure_summary={
                "status": "FAILED",
                "failure_stage": result.failure_stage,
                "failure_category": result.failure_category,
                "root_cause": result.root_cause,
                "error": result.error,
            },
        )
        return result
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"  Unexpected exception during evaluation: {type(e).__name__}: {e}")
        logger.error(f"  Traceback:\n{tb}")
        result = EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
            eval_status="error",
            error=f"{type(e).__name__}: {e}",
            failure_stage=STAGE_TEST_EXECUTION,
            failure_category="internal_error",
            root_cause="unexpected_exception",
            details=build_details(stderr_snippet=tb[:1000]),
        )
        _flush_diag(
            env_check=env_check,
            failure_summary={
                "status": "FAILED",
                "failure_stage": result.failure_stage,
                "failure_category": result.failure_category,
                "root_cause": result.root_cause,
                "error": result.error,
            },
        )
        return result
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True, timeout=30,
        )
        logger.info(f"  Container removed")


def evaluate_batch(
    tasks: list[EvalTask],
    agent_results: list[AgentResult],
    timeout_per_task: int = 600,
    f2p_timeout: int | None = None,
    p2p_timeout: int | None = None,
    tier: str = "lite",
    diagnostics_dir: Path | None = None,
    corp_config: CorpConfig | None = None,
) -> list[EvalResult]:
    """Evaluate a batch of agent results.

    diagnostics_dir: optional root directory for per-task diagnostic artifacts.
    A sub-directory named after each instance_id is created inside it.

    corp_config: opt-in corporate-network configuration; when ``enabled``,
    ``docker run`` injects proxy/CA/mirror env vars and the language profile's
    ``pre_test_hook`` writes any language-specific config files inside the
    container. ``None`` (default) preserves existing behavior.
    """
    task_map = {t.instance_id: t for t in tasks}
    eval_results = []

    for result in agent_results:
        task = task_map.get(result.instance_id)
        if not task:
            eval_results.append(EvalResult(
                instance_id=result.instance_id,
                agent_name=result.agent_name,
                error="Task not found in dataset",
                failure_stage="",
                failure_category="internal_error",
                root_cause="task_not_found",
            ))
            continue

        logger.info(f"Evaluating: {result.instance_id}")
        eval_result = evaluate_single(
            task, result,
            timeout=timeout_per_task,
            f2p_timeout=f2p_timeout,
            p2p_timeout=p2p_timeout,
            tier=tier,
            diagnostics_dir=diagnostics_dir,
            corp_config=corp_config,
        )

        status = "RESOLVED" if eval_result.resolved else "NOT RESOLVED"
        logger.info(
            f"  Result: {status} | "
            f"eval_status={eval_result.eval_status} | "
            f"F2P: {eval_result.fail_to_pass_rate:.0%} | "
            f"P2P: {eval_result.pass_to_pass_rate:.0%}"
        )
        # When a task is NOT RESOLVED, surface why directly in run.log so the
        # user does not have to open eval/<id>/failure_summary.json.
        if not eval_result.resolved:
            if eval_result.error:
                logger.error(f"    Error  : {eval_result.error}")
            if eval_result.failure_stage or eval_result.failure_category:
                logger.error(
                    f"    Detail : stage={eval_result.failure_stage} | "
                    f"category={eval_result.failure_category} | "
                    f"root_cause={eval_result.root_cause}"
                )
            if eval_result.details:
                logger.error(f"    Extra  : {eval_result.details}")
            failed_f2p = [n for n, p in eval_result.fail_to_pass_results.items() if not p]
            failed_p2p = [n for n, p in eval_result.pass_to_pass_results.items() if not p]
            if failed_f2p:
                logger.error(f"    F2P fail ({len(failed_f2p)}): {failed_f2p[:10]}")
            if failed_p2p:
                logger.error(f"    P2P fail ({len(failed_p2p)}): {failed_p2p[:10]}")

        eval_results.append(eval_result)

    return eval_results
