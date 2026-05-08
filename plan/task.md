# Task: Support evaluating Claude Code with a custom vLLM-backed Anthropic-compatible endpoint

## Background

This repository currently supports evaluating Claude Code using its default built-in Anthropic models such as `sonnet`, `opus`, and `haiku`.

I want to additionally support evaluating Claude Code against a custom vLLM model exposed through an Anthropic-compatible API endpoint. I have already verified manually that Claude Code can call this model correctly when the following environment variables are set:

```bash
export ANTHROPIC_BASE_URL="<vllm base url>"
export ANTHROPIC_AUTH_TOKEN="<api key>"
export ANTHROPIC_API_KEY=""
export ANTHROPIC_MODEL="<model id>"
export ANTHROPIC_DEFAULT_OPUS_MODEL="<model id>"
export ANTHROPIC_DEFAULT_SONNET_MODEL="<model id>"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="<model id>"
```

## Goal

Add first-class support for running evaluations with Claude Code using either:

1. The normal Claude Code behavior with built-in Anthropic models.
2. A custom vLLM-backed model configured through environment variables.

Existing Claude Code evaluations using `--model sonnet`, `--model opus`, `--model haiku`, etc. must continue to work exactly as before.

## Desired CLI behavior

Add an option to `scripts/run_eval.py` that allows the user to explicitly request the custom vLLM-backed Claude Code mode.

Suggested interface:

```bash
python scripts/run_eval.py \
  --tier lite \
  --agents claude-code \
  --model <model-id-or-alias> \
  --run-id <id> \
  --verify \
  --claude-code-vllm
```

When `--claude-code-vllm` is provided and the selected agent is `claude-code`, the evaluation should run Claude Code with the vLLM environment variables injected into the Claude Code subprocess environment.

When `--claude-code-vllm` is not provided, behavior should remain unchanged.

## Configuration

Do not hard-code secrets or endpoint values in Python code.

Add config support so the following values can be read from `.env` and/or agent config:

```bash
CLAUDE_CODE_VLLM_BASE_URL=
CLAUDE_CODE_VLLM_AUTH_TOKEN=
CLAUDE_CODE_VLLM_MODEL=
```

Then map them internally to the environment variables Claude Code expects:

```bash
ANTHROPIC_BASE_URL=<CLAUDE_CODE_VLLM_BASE_URL>
ANTHROPIC_AUTH_TOKEN=<CLAUDE_CODE_VLLM_AUTH_TOKEN>
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=<CLAUDE_CODE_VLLM_MODEL>
ANTHROPIC_DEFAULT_OPUS_MODEL=<CLAUDE_CODE_VLLM_MODEL>
ANTHROPIC_DEFAULT_SONNET_MODEL=<CLAUDE_CODE_VLLM_MODEL>
ANTHROPIC_DEFAULT_HAIKU_MODEL=<CLAUDE_CODE_VLLM_MODEL>
```

## Implementation requirements

1. Locate the Claude Code adapter in `src/adapters/`.
2. Add a clean way for the adapter to receive whether vLLM mode is enabled.
3. When launching the Claude Code subprocess, copy the current environment and override only the Anthropic-related variables needed for vLLM mode.
4. Do not mutate `os.environ` globally.
5. Ensure the custom environment is passed only to the Claude Code subprocess.
6. If vLLM mode is enabled but required config values are missing, fail early with a clear error message.
7. Preserve normal Claude Code behavior when vLLM mode is disabled.
8. Include the selected backend/mode in run metadata, for example:

   * `claude_code_backend: "default"` or `"vllm"`
   * `claude_code_vllm_model: "<model id>"` when applicable
9. Make sure resume mode still works.
10. Make sure report generation still works without requiring vLLM config.

## Suggested files to inspect and modify

### `scripts/run_eval.py`

* Add CLI flag such as `--claude-code-vllm`
* Pass this option into the Claude Code adapter or agent config
* Store mode information in metadata

### `src/adapters/`

* Find the Claude Code adapter
* Modify subprocess environment handling
* Keep default behavior unchanged

### `src/core/config.py`

* Add config loading support if needed

### `config/agents/claude_code.yaml`

* Add optional vLLM-related config fields if this project keeps agent-specific settings there

### `.env.example`

* Document the new optional variables

### `README.md` or relevant docs

* Add usage examples for default Claude Code and vLLM-backed Claude Code

## Acceptance criteria

The following command still works exactly as before:

```bash
python scripts/run_eval.py \
  --tier lite \
  --agents claude-code \
  --model sonnet \
  --sample-size 1
```

The following command runs Claude Code against the custom vLLM endpoint:

```bash
python scripts/run_eval.py \
  --tier lite \
  --agents claude-code \
  --model <model-id> \
  --sample-size 1 \
  --claude-code-vllm
```

When `--claude-code-vllm` is enabled, the Claude Code subprocess receives:

```bash
ANTHROPIC_BASE_URL
ANTHROPIC_AUTH_TOKEN
ANTHROPIC_API_KEY=""
ANTHROPIC_MODEL
ANTHROPIC_DEFAULT_OPUS_MODEL
ANTHROPIC_DEFAULT_SONNET_MODEL
ANTHROPIC_DEFAULT_HAIKU_MODEL
```

When `--claude-code-vllm` is disabled, these variables must not be forcibly overridden by the evaluation harness.

## Important caution

Do not break existing model passthrough behavior. `--model` is currently forwarded verbatim to the agent CLI, and that behavior should remain compatible with both default Claude Code models and custom vLLM model IDs.

Also avoid global environment mutation because multiple agents may be evaluated in the same run in the future.
