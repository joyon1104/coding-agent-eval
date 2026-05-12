# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

CLI-driven evaluation harness for AI coding agents (Claude Code, OpenCode, ...). Runs them against SWE-bench instances, verifies generated patches in SWE-bench Docker images, and produces a 6-metric comparison report (S/A/B/C/D/F graded).

The 6 scored metrics are: `task_resolution_rate`, `token_efficiency`, `cost_per_resolved_task`, `e2e_time`, `time_to_first_action`, `convergence_steps`.

Grading thresholds (from `src/reporter/scorer.py`) — lower bound for each grade:

| Metric | S | A | B | C | D | better |
|---|---|---|---|---|---|---|
| task_resolution_rate | ≥60% | ≥45% | ≥30% | ≥20% | ≥10% | higher |
| token_efficiency | ≤50k | ≤100k | ≤200k | ≤400k | ≤800k | lower |
| cost_per_resolved_task | ≤$0.50 | ≤$1.00 | ≤$2.00 | ≤$5.00 | ≤$10.00 | lower |
| e2e_time | ≤60s | ≤120s | ≤300s | ≤600s | ≤1200s | lower |
| time_to_first_action | ≤3s | ≤5s | ≤10s | ≤20s | ≤30s | lower |
| convergence_steps | ≤5 | ≤10 | ≤20 | ≤30 | ≤50 | lower |

## Environment setup

- Python 3.10+; project targets a `.venv` at the repo root.
- Install: `pip install -r requirements.txt` (runtime) or `requirements-dev.txt` (adds `pytest`, `pytest-mock`).
- `pip install -e .` is recommended — imports across the codebase use absolute `src.*` paths (e.g. `from src.core.config import Config`), so running from a directory other than the project root fails without an editable install.
- Secrets live in `.env` (see `.env.example`). `src.core.config.Config()` loads it automatically via `python-dotenv`. Required: `ANTHROPIC_API_KEY` (Claude Code) or `OPENAI_API_KEY` (OpenCode). For vLLM mode: `CLAUDE_CODE_VLLM_BASE_URL`, `CLAUDE_CODE_VLLM_AUTH_TOKEN`, `CLAUDE_CODE_VLLM_MODEL`.

## Common commands

```bash
# Full pipeline (patch generation → Docker verify → report)
# --agents accepts: claude-code, opencode (comma-separated for multi-agent comparison)
python scripts/run_eval.py --tier lite --agents claude-code --model sonnet \
    --sample-size 3 --run-id <id> --verify

# Step 1 only (patch generation); --verify chains all 3 steps automatically
python scripts/run_eval.py --tier lite --agents claude-code --run-id <id>

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

# Tests
pytest
pytest tests/test_ruby_profile.py::TestCheckRspecDesc   # single test class
```

### Subset and image utilities

```bash
# Pull Docker images before eval to avoid mid-run failures
python scripts/prepull_images.py --dataset data/swebench_lite.jsonl
# multi tier needs --tier multi (uses Docker Hub, not GHCR)
python scripts/prepull_images.py --dataset data/swebench_multi.jsonl --tier multi

# Check image availability without downloading (manifest inspect only)
python scripts/check_docker_images.py --tier verified

# Extract lightest-weight instances (smallest image + shortest text)
python scripts/pick_small_instances.py --tier verified --n 10 --per-repo-max 1

# Stratified sampling preserving repo/difficulty/patch-size distribution
python scripts/swebench_sampler.py \
    --input data/swebench_lite.jsonl --output data/swebench_lite_subset.jsonl \
    --size 20 --dataset lite --seed 42

# Run eval on a custom subset JSONL (--input overrides the tier's default path)
python scripts/run_eval.py --tier lite --offline --agents claude-code \
    --input data/swebench_lite_subset.jsonl
```

## Three-step architecture

The pipeline is intentionally split because patch generation and test verification run in **different environments** and communicate via git patches:

1. **Step 1 — agent run** (`scripts/run_eval.py` → `src.runner.orchestrator.Orchestrator`). Clones the target repo to a temp sandbox, checks out `base_commit`, invokes the agent CLI as a subprocess with `cwd=repo_path`, then extracts the agent's changes via `git diff` in `AgentAdapter._extract_patch()`. The sandbox is deleted after; only the patch + telemetry (tokens, cost, turns, time) is persisted.
2. **Step 2 — Docker verification** (`scripts/run_docker_eval.py` → `src.evaluator.docker_evaluator`). Pulls the per-instance SWE-bench image, applies the saved patch, runs FAIL_TO_PASS and PASS_TO_PASS tests.
3. **Step 3 — report** (`scripts/generate_report.py` → `src.reporter`). Joins Step 1 telemetry with Step 2 test outcomes, computes the 6 metrics, assigns grades, emits Markdown/JSON/CSV.

**Key consequence**: Step 1 alone cannot verify correctness — it only confirms a patch was produced. TRR and Regression Safety require Step 2.

## Code layout — where things live

- `src/core/` — `Config` (env-aware YAML merge + `.env`), `EvalTask` / `AgentResult` / `TokenUsage` dataclasses, environment auto-detection (`env_detect.py`: OS, disk, Docker, network → drives tier recommendation and config selection), `run_id.py` (`generate_run_id()` / `parse_run_id()` — format: `{agent}_{model_slug}_{YYYYMMDD-HHMMSS}`).
- `src/dataset/` — `loader.py` (HuggingFace online + local JSONL offline, dispatches on `--offline` flag), `sampler.py` (stratified sampling used by `scripts/swebench_sampler.py`).
- `src/adapters/` — `AgentAdapter` ABC + concrete subprocess-based CLIs. Adding a new agent = new subclass implementing `run()` and `is_available()`, plus registration in `scripts/run_eval.py`'s `AGENT_REGISTRY`.
- `src/runner/orchestrator.py` — drives Step 1. Clones repos via `DiskAwareSandbox`, calls the adapter, saves per-task JSON **immediately** after each task (enables resume). Repos are cloned once into `.repo_cache/` at the project root, then each task gets a fast `git clone --local` copy — avoids repeated full network clones for large repos (e.g. Django ~500 MB). Only workdirs prefixed `cae_*` are ever deleted, so sandbox cleanup is safe on shared machines.
- `src/evaluator/` — `docker_evaluator.py` orchestrates the container lifecycle; `swebench_harness.py` wraps test execution; `patch_extractor.py` validates/normalizes patches. Language-specific behavior lives in `languages/`: `profile.py` defines the `LanguageProfile` ABC; `dispatch.py` maps repos to profiles; `python.py`, `ruby.py`, `go.py`, `java.py`, `javascript.py`, `rust.py`, `c.py`, `cpp.py`, `php.py` are the concrete implementations.
- `src/metrics/` — one file per metric category (accuracy, cost, latency, process). `src/reporter/scorer.py` owns the S/A/B/C/D/F thresholds.
- `config/` — `eval_config.yaml` (tiers, execution limits, model pricing for token→cost fallback); `environments/{common,wsl,native_linux}.yaml` (deep-merged in order: `common.yaml` first, then the env-specific file detected by `env_detect.py`, with later keys winning); `agents/<name>.yaml` (per-agent defaults — filename uses `_` even when CLI name uses `-`).
- `plan/` — design documents (`multilingual_design.md`, `phase1.md`). Read these for intent behind architectural decisions before making structural changes.

## Multi-language support (multi tier)

The `multi` tier (`SWE-bench/SWE-bench_Multilingual`) covers 9 languages (C, C++, Go, Java, JavaScript, PHP, Python, Ruby, Rust) across 41 repos. Language-specific logic (test commands, post-patch recompile hooks) is encapsulated in `LanguageProfile` subclasses in `src/evaluator/languages/`. The dispatch table in `src/evaluator/languages/dispatch.py:REPO_LANGUAGE` maps each repo to its profile.

**Adding a new language**:
1. Create `src/evaluator/languages/<lang>.py` implementing `LanguageProfile`
2. Add `"owner/repo": <Lang>Profile` entries to `REPO_LANGUAGE` in `dispatch.py`
3. Smoke-test: `python scripts/run_eval.py --tier multi --sample-size 1`

**vLLM backend**: pass `--claude-code-vllm` to `run_eval.py` to route Claude Code against a local vLLM-backed Anthropic-compatible endpoint. Requires `CLAUDE_CODE_VLLM_BASE_URL`, `CLAUDE_CODE_VLLM_AUTH_TOKEN`, and `CLAUDE_CODE_VLLM_MODEL` in `.env`; validation is eager (all three must be set before any tasks run).

**Corporate-network mode**: pass `--corp` to `run_eval.py` / `run_docker_eval.py` to inject proxy / CA bundle / package-mirror env vars from `.env` into the agent subprocess (Step 1) AND the Docker container (Step 2 — via `-e` and `-v` on `docker run`). Without `--corp` no environment variables are injected anywhere, preserving current behavior bit-for-bit. Required env vars when enabled: `HTTPS_PROXY`, `CORP_CA_BUNDLE_PATH` (host PEM path, fanned out to `REQUESTS_CA_BUNDLE`/`NODE_EXTRA_CA_CERTS`/`GIT_SSL_CAINFO`/…), `PIP_INDEX_URL`. Optional per-language: `NPM_CONFIG_REGISTRY`, `CORP_MAVEN_MIRROR_URL`, `GOPROXY`/`GOSUMDB`/`GOPRIVATE`, `CORP_GEM_SOURCE`, `CORP_COMPOSER_REPO_URL`, `CORP_CARGO_REGISTRY_URL`, `CORP_APT_MIRROR_URL`. Validation is eager — missing required vars cause an exit before any task runs. Design doc: `plan/corp_network_design.md`. **Note**: this flag does NOT configure the Docker daemon's own proxy (used by `docker pull`); that must be set in `/etc/docker/daemon.json` or `~/.docker/config.json` separately.

**Docker registry for multi tier**: multi instances use Docker Hub (`docker.io/swebench/sweb.eval.x86_64.<id>`) not GHCR. The instance ID also undergoes `__` → `_1776_` substitution. Pass `--tier multi` to any script that calls `get_image_name()` — omitting it silently tries GHCR and fails with auth/not-found errors.

## Key conventions

- **Run-ID format**: auto-generated as `{agent}_{model_slug}_{YYYYMMDD-HHMMSS}` (e.g. `claude-code_sonnet_20260415-164235`). Passing an explicit `--run-id` that matches an existing directory triggers **resume mode**, not overwrite.
- **Resume semantics**: re-running `run_eval.py` with the same `--run-id` reloads `results/runs/<run-id>/patches/*.json` and skips tasks whose saved status is `SUCCESS`. `ERROR` and `FAILED` tasks are retried. This is why task JSON is written per-task, not batched.
- **Dataset tiers** (`local` / `lite` / `verified` / `full` / `multi`) are not just sample-size presets — they also bound Docker image disk budget. Tier is auto-selected from detected disk space (`>= 120 GB` → `full`, `>= 30 GB` → `verified`, else → `lite`) unless `--tier` is passed. `multi` and `local` are never auto-selected.
- **Model flag passthrough**: `--model` is forwarded verbatim to the agent CLI. Claude Code accepts aliases (`sonnet`, `opus`) or full IDs; OpenCode expects `provider/model` (`google/gemini-2.5-flash`).
- **Offline mode** (`--offline`): skips HuggingFace, reads `data/<tier>.jsonl`, assumes Docker images were pre-loaded via `docker load`. `scripts/export_dataset.py` prepares the transfer bundle.
- **Results directory**: `results/runs/<run-id>/{patches,eval,reports}/` plus `metadata.json`. The dashboard reads this tree directly — no DB.
- **`local` tier**: reads `data/swebench_local.jsonl` directly (no HuggingFace). Use `scripts/create_test_data.py` to generate synthetic data for smoke tests.
- **`data/` naming conventions**: `swebench_<tier>.jsonl` is the full tier dataset; `_small` suffix = output of `pick_small_instances.py`; `_subset` suffix = output of `swebench_sampler.py`; `_test` / `_test2` are ad-hoc hand-curated test slices; `_origin` preserves the original download before any local edits.

## Things that are easy to get wrong

- Modifying `pyproject.toml`'s `build-backend` breaks `pip install -e .`. The correct value is `setuptools.build_meta`.
- `src.*` imports mean scripts must run with the project root importable. `scripts/run_eval.py` does this by inserting the parent dir into `sys.path`; new scripts should do the same or rely on the editable install.
- When adding a new agent adapter, the CLI must write JSON to stdout in a shape the adapter parses for `TokenUsage` / cost / `num_turns`. If the CLI doesn't report `total_cost_usd`, the harness falls back to `config/eval_config.yaml:pricing` token rates — add an entry there for any new model ID. `ClaudeCodeAdapter._parse_output()` scans stdout lines in reverse and returns the last parseable JSON object — Claude Code may emit multiple JSON lines during a session.
- SWE-bench Docker images are ~3.4 GB each. `config/eval_config.yaml:tiers.<tier>.docker_images_budget_gb` is the guardrail; exceeding it is a disk failure, not a graceful error. Run `scripts/cleanup.py` between large runs.
- `_extract_patch()` in `base.py` does **not** `.strip()` the diff output intentionally — a blank context line in unified diff is literally `" \n"`, and stripping would corrupt hunk line counts.
- When an agent auto-commits during its session (e.g. OpenCode), `base_sha` captured before the run must be passed to `_extract_patch()` as `base_ref`; otherwise `git diff` against the moved HEAD returns empty.
- Docker image pulls use retryable logic in `src/evaluator/registry_utils.py:RETRYABLE` (exponential backoff on transient registry errors). Permanent failures (image not found, auth) are not retried and surface immediately.
