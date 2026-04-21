"""Docker-based test evaluator using official SWE-bench Docker images.

Uses pre-built images from ghcr.io/epoch-research/swe-bench.eval.x86_64.{instance_id}:latest
which contain:
  - The repo at /testbed, checked out to the correct base commit
  - A conda environment "testbed" with all dependencies pre-installed

Workflow per instance:
  1. Pull the SWE-bench Docker image for the instance
  2. Start container (repo + deps already ready)
  3. Apply test_patch (SWE-bench verification tests)
  4. Apply agent's patch
  5. Run FAIL_TO_PASS tests → should pass if fix is correct
  6. Run PASS_TO_PASS tests → should still pass (regression check)
"""

from __future__ import annotations

import logging
import platform
import subprocess
import tempfile
from pathlib import Path

from src.core.models import AgentResult, EvalTask
from src.evaluator.swebench_harness import EvalResult

logger = logging.getLogger("coding-agent-eval")

ARCH = "x86_64" if platform.machine() in ("x86_64", "AMD64") else "arm64"
IMAGE_REGISTRY = "ghcr.io/epoch-research"
IMAGE_PREFIX = f"swe-bench.eval.{ARCH}"

# Conda activation prefix for all commands inside the container
CONDA_ACTIVATE = "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed"


def get_image_name(instance_id: str) -> str:
    """Get the SWE-bench Docker image name for an instance."""
    return f"{IMAGE_REGISTRY}/{IMAGE_PREFIX}.{instance_id}:latest"


def image_exists_locally(image_name: str) -> bool:
    """Check if a Docker image exists locally."""
    result = subprocess.run(
        ["docker", "images", "-q", image_name],
        capture_output=True, text=True, timeout=10,
    )
    return bool(result.stdout.strip())


def pull_image(instance_id: str) -> bool:
    """Pull the SWE-bench Docker image for an instance."""
    image = get_image_name(instance_id)

    if image_exists_locally(image):
        logger.info(f"  Image already exists: {image}")
        return True

    logger.info(f"  Pulling image: {image}")
    result = subprocess.run(
        ["docker", "pull", image],
        capture_output=True, text=True, timeout=600,
    )

    if result.returncode != 0:
        logger.error(f"  Pull failed: {result.stderr[:500]}")
        return False

    logger.info(f"  Pull complete: {image}")
    return True


def _docker_exec(container_id: str, cmd: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """Execute a command inside the container with conda testbed env activated."""
    full_cmd = f"{CONDA_ACTIVATE} && {cmd}"
    return subprocess.run(
        ["docker", "exec", container_id, "bash", "-c", full_cmd],
        capture_output=True, text=True, timeout=timeout,
    )


def _parse_test_output(output: str, test_names: list[str]) -> dict[str, bool]:
    """Parse Django test runner / pytest output to determine pass/fail per test.

    Django format: "test_method (module.Class) ... ok"
                   "test_method (module.Class) ... FAIL"
    pytest format: "module/test_file.py::TestClass::test_method PASSED"
    """
    results = {}

    for test_name in test_names:
        test_name_stripped = test_name.strip()

        # Parse "method_name (module.Class)" format
        if " (" in test_name_stripped and test_name_stripped.endswith(")"):
            method, class_path = test_name_stripped.rsplit(" (", 1)
            class_path = class_path.rstrip(")")
            search_terms = [method, f"{class_path}.{method}", test_name_stripped]
        else:
            search_terms = [test_name_stripped, test_name_stripped.split(".")[-1]]

        passed = False
        found = False

        for term in search_terms:
            for line in output.split("\n"):
                if term not in line:
                    continue
                found = True
                # Django: "test_name ... ok" or "test_name ... FAIL"
                if "... ok" in line or " ok" in line.split("...")[-1] if "..." in line else False:
                    passed = True
                    break
                elif "PASSED" in line:
                    passed = True
                    break
                elif "FAIL" in line or "ERROR" in line:
                    passed = False
                    break
            if found:
                break

        if not found:
            # Check overall test result as fallback
            last_lines = "\n".join(output.split("\n")[-10:])
            if "\nOK" in last_lines and "FAILED" not in last_lines:
                passed = True

        results[test_name] = passed

    return results


def _run_tests_in_container(
    container_id: str,
    test_names: list[str],
    timeout: int = 300,
) -> dict[str, bool]:
    """Run tests inside the SWE-bench container.

    Converts test names and runs them using Django's runtests.py or pytest.
    For Django, extracts unique test modules to pass to runtests.py, then
    parses per-test results from the verbose output.
    """
    if not test_names:
        return {}

    # Convert "method (module.Class)" → "module.Class.method" for Django runtests
    converted = []
    for t in test_names:
        t = t.strip()
        if " (" in t and t.endswith(")"):
            method, cls = t.rsplit(" (", 1)
            converted.append(f"{cls.rstrip(')')}.{method}")
        elif t.startswith("test_") or "." in t:
            converted.append(t)
        # Skip non-standard test names (e.g. "#21962 - adding html escape...")

    # For Django runtests.py, pass unique test modules (not individual methods)
    # to avoid command-line length issues with 100+ tests
    modules = sorted(set(
        ".".join(c.split(".")[:-1])  # module.Class (drop method)
        for c in converted
        if "." in c
    ))
    test_str = " ".join(modules)

    # Write test modules to a file to avoid shell escaping issues
    test_list_file = _write_patch_file("\n".join(modules) + "\n")
    test_list_txt = test_list_file + ".txt"
    Path(test_list_file).rename(test_list_txt)
    try:
        subprocess.run(
            ["docker", "cp", test_list_txt, f"{container_id}:/tmp/test_modules.txt"],
            capture_output=True, timeout=10,
        )
    finally:
        Path(test_list_txt).unlink(missing_ok=True)

    test_cmd = (
        "cd /testbed && "
        "MODULES=$(cat /tmp/test_modules.txt | tr '\\n' ' ') && "
        "if [ -f tests/runtests.py ]; then "
        "  python tests/runtests.py $MODULES --verbosity 2 2>&1; "
        "else "
        "  python -m pytest $MODULES -v 2>&1; "
        "fi"
    )

    try:
        result = _docker_exec(container_id, test_cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning(f"  Test execution timed out after {timeout}s")
        return {t: False for t in test_names}

    output = result.stdout + result.stderr
    logger.info(f"  Test exit code: {result.returncode}")
    logger.info(f"  Test output (last 500 chars): {output[-500:]}")

    return _parse_test_output(output, test_names)


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
) -> EvalResult:
    """Evaluate a single agent result using the SWE-bench Docker image."""
    if not agent_result.patch:
        return EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
            resolved=False,
            error="No patch generated",
        )

    image = get_image_name(task.instance_id)

    # 1. Pull image
    if not pull_image(task.instance_id):
        return EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
            error=f"Failed to pull image: {image}",
        )

    container_name = f"cape_{task.instance_id}"
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
                error=f"Container start failed: {result.stderr[:300]}",
            )

        container_id = result.stdout.strip()[:12]
        logger.info(f"  Container started: {container_id}")

        # 3. Apply test_patch (SWE-bench verification tests)
        if task.test_patch:
            patch_path = _write_patch_file(task.test_patch)
            subprocess.run(
                ["docker", "cp", patch_path, f"{container_name}:/tmp/test.patch"],
                capture_output=True, timeout=10,
            )
            result = _docker_exec(
                container_name, "cd /testbed && git apply /tmp/test.patch", timeout=30,
            )
            if result.returncode != 0:
                result = _docker_exec(
                    container_name, "cd /testbed && patch -p1 < /tmp/test.patch", timeout=30,
                )
            Path(patch_path).unlink(missing_ok=True)
            if result.returncode != 0:
                logger.warning(f"  test_patch apply warning: {result.stderr[:200]}")

        # 4. Apply agent's patch
        #    Official SWE-bench sequence: `git apply` → `git apply --3way` (blob-SHA merge).
        #    Patch content is never modified; --3way only changes the apply algorithm to use
        #    the patch header's blob SHAs for context resolution, recovering from test_patch
        #    drift without altering the agent's submission.
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

        Path(patch_path).unlink(missing_ok=True)

        if result.returncode != 0:
            detail = (result.stderr.strip() or result.stdout.strip() or "(no output)")[:500]
            return EvalResult(
                instance_id=task.instance_id,
                agent_name=agent_result.agent_name,
                resolved=False,
                error=f"Agent patch apply failed: {detail}",
            )

        logger.info(f"  Patch applied successfully")

        # 5. Run FAIL_TO_PASS tests
        logger.info(f"  Running FAIL_TO_PASS tests ({len(task.FAIL_TO_PASS)})...")
        f2p_results = _run_tests_in_container(
            container_name, task.FAIL_TO_PASS, timeout=timeout,
        )

        # 6. Run PASS_TO_PASS tests
        logger.info(f"  Running PASS_TO_PASS tests ({len(task.PASS_TO_PASS)})...")
        p2p_results = _run_tests_in_container(
            container_name, task.PASS_TO_PASS, timeout=timeout,
        )

        # Resolved = all FAIL_TO_PASS tests now pass
        all_f2p_pass = bool(f2p_results) and all(f2p_results.values())

        return EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
            resolved=all_f2p_pass,
            fail_to_pass_results=f2p_results,
            pass_to_pass_results=p2p_results,
        )

    except subprocess.TimeoutExpired:
        return EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
            error=f"Timeout after {timeout}s",
        )
    except Exception as e:
        return EvalResult(
            instance_id=task.instance_id,
            agent_name=agent_result.agent_name,
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
        eval_result = evaluate_single(task, result, timeout=timeout_per_task)

        status = "RESOLVED" if eval_result.resolved else "NOT RESOLVED"
        logger.info(
            f"  Result: {status} | "
            f"F2P: {eval_result.fail_to_pass_rate:.0%} | "
            f"P2P: {eval_result.pass_to_pass_rate:.0%}"
        )

        eval_results.append(eval_result)

    return eval_results
