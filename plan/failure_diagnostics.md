# Task: Enhance SWE-bench evaluation observability and environment failure diagnostics

## Background

The current evaluation pipeline provides insufficient visibility into why individual SWE-bench tasks fail.

For example, many failed tasks only produce generic outputs such as:

```json
{
  "status": "FAILED",
  "error": "No patch generated"
}
```

This makes it difficult to determine the actual root cause.

The system currently cannot clearly distinguish between:

* Actual model reasoning/coding failures
* Agent execution crashes
* Patch extraction failures
* Docker/container setup failures
* Dependency installation failures
* Network/proxy issues
* Corporate registry issues
* Internal infrastructure errors

As a result, evaluation reliability and benchmarking accuracy are difficult to trust.

---

## Important operational context

This system is executed inside a corporate/private environment.

Unlike public internet environments, containerized dependency installation may require:

* Internal npm registry configuration
* Internal pip index configuration
* Corporate proxy settings
* Custom CA certificates
* Restricted outbound networking

Therefore, some evaluations may fail before tests even execute.

Examples include:

* `pip install` failure
* `npm install` failure
* SSL verification failure
* apt package download failure
* Registry authentication failure
* Network timeout during dependency resolution

These should be classified as infrastructure/environment failures, NOT model capability failures.

A model should not be evaluated as “failed to solve the task” when the benchmark environment itself could not be initialized successfully.

---

# Goals

## Goal 1 — Improve failure observability

For every failed evaluation instance, provide detailed structured failure diagnostics.

The system should clearly answer:

* Where did the failure occur?
* Which component failed?
* Was this a model failure or environment failure?
* Did the container initialize successfully?
* Were dependencies installed correctly?
* Did tests actually execute?

---

## Goal 2 — Add environment/dependency diagnostics

Before running SWE-bench verification tests, explicitly validate and record whether the container environment was successfully prepared.

The system should detect and report:

* dependency installation failures
* registry access problems
* SSL/proxy issues
* timeout issues
* missing package managers
* network failures
* container startup failures

---

# Required failure classification system

Introduce structured failure classification.

## failure_stage

Suggested values:

* `repo_clone`
* `sandbox_setup`
* `agent_execution`
* `patch_extraction`
* `docker_pull`
* `container_startup`
* `dependency_installation`
* `test_execution`
* `report_generation`

---

## failure_category

Suggested values:

* `model_failure`
* `environment_failure`
* `network_failure`
* `registry_failure`
* `docker_failure`
* `dependency_failure`
* `timeout`
* `configuration_error`
* `internal_error`

---

## root_cause

Machine-readable identifiers.

Examples:

* `no_patch_generated`
* `git_diff_empty`
* `pip_install_failed`
* `npm_install_failed`
* `ssl_verification_failed`
* `docker_image_pull_timeout`
* `test_command_timeout`
* `container_crashed`
* `missing_dependency`
* `proxy_connection_failed`

---

# Desired output examples

## Example — dependency failure

```json
{
  "status": "FAILED",
  "failure_stage": "dependency_installation",
  "failure_category": "environment_failure",
  "root_cause": "pip_install_failed",
  "details": {
    "command": "pip install -r requirements.txt",
    "stderr_snippet": "SSL certificate verification failed",
    "exit_code": 1
  }
}
```

---

## Example — empty patch

```json
{
  "status": "FAILED",
  "failure_stage": "patch_extraction",
  "failure_category": "model_failure",
  "root_cause": "git_diff_empty"
}
```

---

# Environment verification requirements

Before executing SWE-bench tests:

1. Verify container startup succeeded
2. Verify required package managers exist
3. Capture dependency installation logs
4. Capture stdout/stderr separately
5. Detect installation failures explicitly
6. Detect SSL/proxy/registry issues explicitly
7. Detect timeout separately from generic failures
8. Persist all diagnostics as per-task artifacts

---

# Required logging artifacts

Persist structured logs such as:

```text
results/runs/<run-id>/eval/<task-id>/
  environment_check.json
  dependency_install.log
  docker_setup.log
  test_stdout.log
  test_stderr.log
  failure_summary.json
```

---

# Reporting improvements

The final report should visually and structurally distinguish between:

* Model reasoning failures
* Infrastructure failures
* Dependency installation failures
* Corporate environment failures

Example:

| Failure Type                     | Count |
| -------------------------------- | ----- |
| Model failures                   | 12    |
| Dependency installation failures | 4     |
| SSL/proxy failures               | 3     |
| Docker failures                  | 1     |

Infrastructure-related failures should not be mixed together with actual model capability failures.

---

# Suggested implementation areas

## `src/evaluator/docker_evaluator.py`

Add structured environment validation and failure classification.

---

## `src/evaluator/swebench_harness.py`

Capture dependency installation and test execution diagnostics.

---

## `src/runner/orchestrator.py`

Persist richer per-task execution metadata and failure artifacts.

---

## `src/adapters/`

Improve subprocess stderr/stdout capture and structured error handling.

---

## `src/reporter/`

Add infrastructure-failure aggregation and reporting.

---

# Acceptance criteria

## Case 1 — Actual model failure

The report clearly indicates:

* environment setup succeeded
* tests executed successfully
* model-generated patch failed tests

---

## Case 2 — Corporate registry failure

The report clearly indicates:

* dependency installation failed
* exact command failed
* SSL/proxy/registry issue detected
* benchmark environment failed before meaningful evaluation

---

## Case 3 — Docker failure

The report clearly indicates:

* Docker image pull/startup failure
* benchmark never actually executed

---

## Case 4 — Empty patch generation

The report clearly indicates:

* Claude Code executed successfully
* no meaningful git diff was produced
* patch extraction stage failed
