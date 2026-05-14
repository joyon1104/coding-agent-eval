"""Configuration loader with environment-aware merging."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Side-effect import: setup_tmpdir() runs at module load so any tempfile usage
# downstream (sandbox.mkdtemp, docker_evaluator NamedTemporaryFile, etc.) picks
# up TMPDIR from .env. Must come before src.core.env_detect to be safe.
from src.core import tmpdir as _tmpdir  # noqa: F401
from src.core.env_detect import detect_environment, EnvironmentInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_env_config(env_info: EnvironmentInfo | None = None) -> dict:
    """Load environment-specific config, merged with common."""
    common = _load_yaml(CONFIG_DIR / "environments" / "common.yaml")

    if env_info is None:
        env_info = detect_environment()

    env_file = CONFIG_DIR / "environments" / f"{env_info.os_type}.yaml"
    env_cfg = _load_yaml(env_file)

    # Handle extends
    env_cfg.pop("extends", None)

    return _deep_merge(common, env_cfg)


def load_eval_config() -> dict:
    return _load_yaml(CONFIG_DIR / "eval_config.yaml")


def load_agent_config(agent_name: str) -> dict:
    path = CONFIG_DIR / "agents" / f"{agent_name.replace('-', '_')}.yaml"
    if not path.exists():
        path = CONFIG_DIR / "agents" / f"{agent_name}.yaml"
    return _load_yaml(path)


class Config:
    """Central configuration object."""

    def __init__(self, tier: str | None = None, offline: bool = False):
        load_dotenv(PROJECT_ROOT / ".env")

        self.env_info = detect_environment(str(PROJECT_ROOT))
        self.env_config = load_env_config(self.env_info)
        self.eval_config = load_eval_config()

        self.tier = tier or self.env_info.recommended_tier
        self.offline = offline
        self.project_root = PROJECT_ROOT

    @property
    def tier_config(self) -> dict:
        return self.eval_config.get("tiers", {}).get(self.tier, {})

    @property
    def execution_config(self) -> dict:
        return self.eval_config.get("execution", {})

    @property
    def pricing_config(self) -> dict:
        return self.eval_config.get("pricing", {})

    def get(self, *keys: str, default: Any = None) -> Any:
        """Dot-path accessor: config.get('execution', 'max_turns_per_task')"""
        d = self.eval_config
        for k in keys:
            if isinstance(d, dict):
                d = d.get(k, default)
            else:
                return default
        return d
