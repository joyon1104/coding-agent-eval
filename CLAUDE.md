# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

CLI-driven evaluation harness for AI coding agents (Claude Code, OpenCode, ...). Runs them against SWE-bench instances, verifies generated patches in SWE-bench Docker images, and produces a 7-metric comparison report (S/A/B/C/D/F graded).

## Environment setup

- Python 3.10+; project targets a `.venv` at the repo root.
- Install: `pip install -r requirements.txt` (runtime) or `requirements-dev.txt` (adds `pytest`, `pytest-mock`).
- `pip install -e .` is recommended — imports across the codebase use absolute `src.*` paths (e.g. `from src.core.config import Config`), so running from a directory other than the project root fails without an editable install.
- Secrets live in `.env` (see `.env.example`). `src.core.config.Config()` loads it automatically via `python-dotenv`.

## Common commands

```bash
# Full pipeline (patch generation → Docker verify → report)
python scripts/run_eval.py --tier micro --agents claude-code --model sonnet \
    --sample-size 3 --run-id <id> --verify

# Step 1 only (patch generation)
python scripts/run_eval.py --tier micro --agents claude-code --run-id <id>

# Step 2 (Docker verification, on existing run)
python scripts/run_docker_eval.py --run-id <id> --agent claude-code

# Step 3 (report only, or merge multiple runs)
python scripts/generate_report.py --run-id <id> --format markdown,json
python scripts/generate_report.py --run-id merged --merge-dirs results/runs/a,results/runs/b

# Mock-agent smoke test (no API key, no Docker)
python scripts/create_test_data.py && python scripts/run_e2e_test.py

# Dashboard (stdlib HTTP server, reads results/runs/)
python dashboard/server.py --port 8080

# Cleanup accumulated Docker images / temp files
python scripts/cleanup.py

# Tests (tests/ is currently scaffolded but empty)
pytest
pytest tests/test_file.py::test_name   # single test
```

## Three-step architecture

The pipeline is intentionally split because patch generation and test verification run in **different environments** and communicate via git patches:

1. **Step 1 — agent run** (`scripts/run_eval.py` → `src.runner.orchestrator.Orchestrator`). Clones the target repo to a temp sandbox, checks out `base_commit`, invokes the agent CLI as a subprocess with `cwd=repo_path`, then extracts the agent's changes via `git diff` in `AgentAdapter._extract_patch()`. The sandbox is deleted after; only the patch + telemetry (tokens, cost, turns, time) is persisted.
2. **Step 2 — Docker verification** (`scripts/run_docker_eval.py` → `src.evaluator.docker_evaluator`). Pulls the per-instance SWE-bench image `ghcr.io/epoch-research/swe-bench.eval.x86_64.{instance_id}:latest` (fully-provisioned env at `/testbed`), applies the saved patch, runs FAIL_TO_PASS and PASS_TO_PASS tests.
3. **Step 3 — report** (`scripts/generate_report.py` → `src.reporter`). Joins Step 1 telemetry with Step 2 test outcomes, computes the 7 metrics, assigns grades, emits Markdown/JSON/CSV.

**Key consequence**: Step 1 alone cannot verify correctness — it only confirms a patch was produced. TRR and Regression Safety require Step 2.

## Code layout — where things live

- `src/core/` — `Config` (env-aware YAML merge + `.env`), `EvalTask` / `AgentResult` / `TokenUsage` dataclasses, environment auto-detection (`env_detect.py`: OS, disk, Docker, network → drives tier recommendation and config selection).
- `src/adapters/` — `AgentAdapter` ABC + concrete subprocess-based CLIs. Adding a new agent = new subclass implementing `run()` and `is_available()`, plus registration in `scripts/run_eval.py`'s `AGENT_REGISTRY`.
- `src/runner/orchestrator.py` — drives Step 1. Clones repos via `DiskAwareSandbox`, calls the adapter, saves per-task JSON **immediately** after each task (enables resume).
- `src/evaluator/` — `docker_evaluator.py` orchestrates the container lifecycle; `swebench_harness.py` wraps test execution; `patch_extractor.py` validates/normalizes patches.
- `src/metrics/` — one file per metric category (accuracy, cost, latency, process). `src/reporter/scorer.py` owns the S/A/B/C/D/F thresholds.
- `config/` — `eval_config.yaml` (tiers, execution limits, model pricing for token→cost fallback); `environments/{common,wsl,native_linux}.yaml` (deep-merged by `env_detect` result); `agents/<name>.yaml` (per-agent defaults — filename uses `_` even when CLI name uses `-`).

## Key conventions

- **Resume semantics**: re-running `run_eval.py` with the same `--run-id` reloads `results/runs/<run-id>/patches/*.json` and skips tasks whose saved status is `SUCCESS`. Error results are retried. This is why task JSON is written per-task, not batched.
- **Dataset tiers** (`micro` / `mini` / `full`) are not just sample-size presets — they also bound Docker image disk budget. Tier is auto-selected from detected disk space unless `--tier` is passed.
- **Model flag passthrough**: `--model` is forwarded verbatim to the agent CLI. Claude Code accepts aliases (`sonnet`, `opus`) or full IDs; OpenCode expects `provider/model` (`google/gemini-2.5-flash`).
- **Offline mode** (`--offline`): skips HuggingFace, reads `data/<tier>.jsonl`, assumes Docker images were pre-loaded via `docker load`. `scripts/export_dataset.py` prepares the transfer bundle.
- **Results directory**: `results/runs/<run-id>/{patches,eval,reports}/` plus `metadata.json`. The dashboard reads this tree directly — no DB.

## Things that are easy to get wrong

- Modifying `pyproject.toml`'s `build-backend` breaks `pip install -e .`. The correct value is `setuptools.build_meta`.
- `src.*` imports mean scripts must run with the project root importable. `scripts/run_eval.py` does this by inserting the parent dir into `sys.path`; new scripts should do the same or rely on the editable install.
- When adding a new agent adapter, the CLI must write JSON to stdout in a shape the adapter parses for `TokenUsage` / cost / `num_turns`. If the CLI doesn't report `total_cost_usd`, the harness falls back to `config/eval_config.yaml:pricing` token rates — add an entry there for any new model ID.
- SWE-bench Docker images are ~3.4 GB each. `config/eval_config.yaml:tiers.<tier>.docker_images_budget_gb` is the guardrail; exceeding it is a disk failure, not a graceful error. Run `scripts/cleanup.py` between large runs.
