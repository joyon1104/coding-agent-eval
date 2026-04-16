"""Run ID generation and parsing utilities."""

from __future__ import annotations

import re
from datetime import datetime

# Model name normalization: full name -> short slug
_MODEL_SLUGS = {
    "claude-opus-4-6": "opus-4",
    "claude-opus-4-6[1m]": "opus-4",
    "claude-sonnet-4-6": "sonnet-4",
    "claude-haiku-4-5-20251001": "haiku-4",
    "claude-sonnet-4-20250514": "sonnet-4",
    "claude-opus-4-20250514": "opus-4",
}


def normalize_model(model_raw: str | None) -> str:
    """Convert a full model identifier to a short slug for folder naming.

    Examples:
        "sonnet" -> "sonnet"
        "opus" -> "opus"
        "claude-sonnet-4-6" -> "sonnet-4"
        "google/gemini-2.5-flash" -> "gemini-2.5-flash"
        "openai/gpt-4o" -> "gpt-4o"
        None -> "default"
    """
    if not model_raw:
        return "default"

    # Check lookup table
    if model_raw in _MODEL_SLUGS:
        return _MODEL_SLUGS[model_raw]

    # Strip provider prefix (e.g. "google/gemini-2.5-flash" -> "gemini-2.5-flash")
    if "/" in model_raw:
        model_raw = model_raw.split("/", 1)[1]

    # Strip common prefixes
    for prefix in ("claude-", "gpt-"):
        if model_raw.startswith(prefix) and model_raw in _MODEL_SLUGS:
            return _MODEL_SLUGS[model_raw]

    return model_raw


def generate_run_id(agent_name: str, model: str | None = None) -> str:
    """Generate a structured run ID.

    Format: {agent}_{model_slug}_{YYYYMMDD-HHMMSS}

    Examples:
        generate_run_id("claude-code", "sonnet")
        -> "claude-code_sonnet_20260415-164235"

        generate_run_id("opencode", "google/gemini-2.5-flash")
        -> "opencode_gemini-2.5-flash_20260415-164235"
    """
    model_slug = normalize_model(model)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{agent_name}_{model_slug}_{timestamp}"


def parse_run_id(run_id: str) -> dict:
    """Parse a structured run ID into components.

    Returns dict with 'agent', 'model', 'timestamp' keys.
    For legacy/unparseable run IDs, fields are set to None.
    """
    # Try new format: {agent}_{model}_{YYYYMMDD-HHMMSS}
    match = re.match(r"^(.+?)_(.+?)_(\d{8}-\d{6})$", run_id)
    if match:
        return {
            "agent": match.group(1),
            "model": match.group(2),
            "timestamp": match.group(3),
        }

    return {
        "agent": None,
        "model": None,
        "timestamp": None,
    }
