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
"""

from __future__ import annotations

import logging
import platform
import re
import subprocess
import tempfile
import time
from pathlib import Path

from src.core.models import AgentResult, EvalTask
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


def _run_tests_in_container(
    container_id: str,
    test_names: list[str],
    profile: LanguageProfile,
    task: EvalTask,
    timeout: int = 300,
) -> dict[str, bool]:
    """Run tests inside the SWE-bench container via the language profile.

    profile.build_test_command() constructs the shell command (and may stage
    input files via docker cp). profile.parse_test_output() maps runner output
    to pass/fail per test name. All language-specific logic lives in the profile.
    """
    if not test_names:
        return {}

    cmd = profile.build_test_command(test_names, task, container_id)

    try:
        result = _docker_exec(
            container_id, cmd, timeout=timeout, prefix=profile.shell_prefix()
        )
        output = (result.stdout or "") + (result.stderr or "")
        logger.info(f"  Test exit code: {result.returncode}")
    except subprocess.TimeoutExpired as e:
        # Salvage whatever the runner wrote before we killed it — tests that
        # completed before the timeout still have valid results in the buffer.
        partial_stdout = (e.stdout or b"").decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        partial_stderr = (e.stderr or b"").decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        output = partial_stdout + partial_stderr
        logger.warning(
            f"  Test execution timed out after {timeout}s; parsing {len(output)} "
            f"chars of partial output (completed tests keep real results; rest are False)"
        )

    logger.info(f"  Test output (last 500 chars): {output[-500:]}")
    outcomes = profile.parse_test_output(output, "", test_names)
    return {o.name: o.passed for o in outcomes}


def _write_patch_file(patch_text: str) -> str:
    """Write patch to a temp file, ensuring trailing newline."""
    if not patch_text.endswith("\n"):
        patch_text += "\n"
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False)
    f.write(patch_text)
    f.close()
    return f.name


def evaluate_single(
    task: EvalTask,
    agent_result: AgentResult,
    timeout: int = 600,
    f2p_timeout: int | None = None,
    p2p_timeout: int | None = None,
    tier: str = "lite",
) -> EvalResult:
    """Evaluate a single agent result using the SWE-bench Docker image.

    `timeout` is the default per-phase budget. `f2p_timeout` / `p2p_timeout`
    override it for the respective test batches — P2P can hold hundreds of
    tests on Django instances, so it gets a more generous default (1.5x).
    `tier` selects the language profile: "lite"/"verified"/"full" → Python;
    "multi" → dispatch by repo.
    """
    f2p_timeout = f2p_timeout if f2p_timeout is not None else timeout
    p2p_timeout = p2p_timeout if p2p_timeout is not None else int(timeout * 1.5)

    profile = get_profile(task, tier)

    # No patch from agent → fail (agent-side issue, not environment).
    if not agent_result.patch:
        return EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
            resolved=False,
            eval_status="fail",
            error="No patch generated",
        )

    image = profile.get_image_name(task.instance_id)

    # 1. Pull image — environmental dependency, classify failures as "error".
    if not pull_image(task.instance_id, tier=tier):
        return EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
            eval_status="error",
            error=f"Failed to pull image: {image}",
        )

    container_name = f"cae_{task.instance_id}"
    container_id = None

    try:
        # 2. Start container (image already has repo at /testbed with deps)
        logger.info(f"  Starting container from {image}")

        # Remove existing container if any
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True, timeout=10,
        )

        result = subprocess.run(
            ["docker", "run", "-d", "--name", container_name,
             image, "tail", "-f", "/dev/null"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return EvalResult(
                instance_id=task.instance_id,
                agent_name=agent_result.agent_name,
                eval_status="error",
                error=f"Container start failed: {result.stderr[:300]}",
            )

        container_id = result.stdout.strip()[:12]
        logger.info(f"  Container started: {container_id}")

        # 3. Apply agent's patch — three-tier fallback (patch content never modified):
        #    (1) strict `git apply`
        #    (2) `git apply --3way` (blob-SHA merge; recovers from test_patch drift)
        #    (3) `git apply --3way --ignore-whitespace` (tolerates whitespace drift)
        patch_path = _write_patch_file(agent_result.patch)
        subprocess.run(
            ["docker", "cp", patch_path, f"{container_name}:/tmp/agent.patch"],
            capture_output=True, timeout=10,
        )

        result = _docker_exec(
            container_name,
            "cd /testbed && git apply --verbose /tmp/agent.patch",
            timeout=30,
        )
        if result.returncode != 0:
            logger.info(f"  git apply failed, retrying with --3way...")
            result = _docker_exec(
                container_name,
                "cd /testbed && git apply --verbose --3way /tmp/agent.patch",
                timeout=30,
            )
        if result.returncode != 0:
            logger.info(f"  --3way failed, retrying with --ignore-whitespace...")
            result = _docker_exec(
                container_name,
                "cd /testbed && git apply --verbose --3way --ignore-whitespace /tmp/agent.patch",
                timeout=30,
            )

        Path(patch_path).unlink(missing_ok=True)

        if result.returncode != 0:
            detail = _format_apply_failure(result, agent_result.patch)
            # Patch apply failures are agent-side issues (malformed patch,
            # context mismatch from agent's poor edits). Classify as "fail".
            return EvalResult(
                instance_id=task.instance_id,
                agent_name=agent_result.agent_name,
                resolved=False,
                eval_status="fail",
                error=f"Agent patch apply failed: {detail}",
            )

        logger.info(f"  Patch applied successfully")

        # 4. SWE-bench official guard: reset paths that test_patch will touch.
        # Some agents include changes to test files; without this reset, those
        # changes would conflict with test_patch (which we apply next), even
        # though SWE-bench's contract is that test files are owned by
        # test_patch and the agent is evaluated only on its production-code
        # changes. Mirroring the official harness order (agent → reset →
        # test_patch) keeps production changes intact while restoring test
        # files so test_patch always applies cleanly.
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
                # `|| true`: paths newly created by test_patch don't exist in
                # HEAD, so checkout prints a warning for them. That's expected.
                _docker_exec(
                    container_name,
                    "cd /testbed && xargs -d '\\n' git checkout HEAD -- "
                    "< /tmp/test_paths.txt 2>&1 || true",
                    timeout=30,
                )
                logger.info(f"  Reset {len(test_paths)} test path(s) before test_patch")

        # 5. Apply test_patch (SWE-bench verification tests).
        # Resets above guarantee target files are at base, so a strict
        # `git apply` should succeed for the SWE-bench-curated test_patch.
        # We keep the `--3way` fallback as a safety net for unusual cases.
        if task.test_patch:
            patch_path = _write_patch_file(task.test_patch)
            subprocess.run(
                ["docker", "cp", patch_path,
                 f"{container_name}:/tmp/test.patch"],
                capture_output=True, timeout=10,
            )
            result = _docker_exec(
                container_name,
                "cd /testbed && git apply --verbose /tmp/test.patch",
                timeout=30,
            )
            if result.returncode != 0:
                logger.info("  test_patch apply failed, retrying with --3way...")
                result = _docker_exec(
                    container_name,
                    "cd /testbed && git apply --verbose --3way /tmp/test.patch",
                    timeout=30,
                )
            Path(patch_path).unlink(missing_ok=True)
            if result.returncode != 0:
                detail = (result.stderr.strip() or result.stdout.strip()
                          or "(no output)")[:300]
                logger.error(f"  test_patch apply failed: {detail}")

        # 6. Post-patch hook (language-specific; no-op for Python, recompile for C++)
        profile.post_patch_hook(container_name)

        # 7. Run FAIL_TO_PASS tests
        logger.info(f"  Running FAIL_TO_PASS tests ({len(task.FAIL_TO_PASS)})...")
        f2p_results = _run_tests_in_container(
            container_name, task.FAIL_TO_PASS, profile, task, timeout=f2p_timeout,
        )

        # 8. Run PASS_TO_PASS tests
        logger.info(f"  Running PASS_TO_PASS tests ({len(task.PASS_TO_PASS)})...")
        p2p_results = _run_tests_in_container(
            container_name, task.PASS_TO_PASS, profile, task, timeout=p2p_timeout,
        )

        # Reaching this point means both F2P and P2P test batches were
        # executed → eval_status="success". Resolved is the stricter
        # conjunction: ALL F2P AND ALL P2P tests passed.
        all_f2p_pass = all(f2p_results.values()) if f2p_results else False
        all_p2p_pass = (all(p2p_results.values()) if p2p_results else True)
        resolved = all_f2p_pass and all_p2p_pass

        error_note = ""
        if not task.FAIL_TO_PASS:
            error_note = "no FAIL_TO_PASS tests in dataset row; resolution unverifiable"
            resolved = False

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
        # Test runner exceeded the wall clock. Environmental, not agent fault.
        return EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
            eval_status="error",
            error=f"Timeout after {timeout}s",
        )
    except Exception as e:
        return EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
            eval_status="error",
            error=str(e),
        )
    finally:
        # Cleanup container
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
) -> list[EvalResult]:
    """Evaluate a batch of agent results."""
    task_map = {t.instance_id: t for t in tasks}
    eval_results = []

    for result in agent_results:
        task = task_map.get(result.instance_id)
        if not task:
            eval_results.append(EvalResult(
                instance_id=result.instance_id,
                agent_name=result.agent_name,
                error="Task not found in dataset",
            ))
            continue

        logger.info(f"Evaluating: {result.instance_id}")
        eval_result = evaluate_single(
            task, result,
            timeout=timeout_per_task,
            f2p_timeout=f2p_timeout,
            p2p_timeout=p2p_timeout,
            tier=tier,
        )

        status = "RESOLVED" if eval_result.resolved else "NOT RESOLVED"
        logger.info(
            f"  Result: {status} | "
            f"F2P: {eval_result.fail_to_pass_rate:.0%} | "
            f"P2P: {eval_result.pass_to_pass_rate:.0%}"
        )

        eval_results.append(eval_result)

    return eval_results
