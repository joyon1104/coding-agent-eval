"""Dataset loader: online (HuggingFace) and offline (local JSONL)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src.core.config import Config, PROJECT_ROOT
from src.core.models import EvalTask


def load_from_jsonl(path: Path) -> list[dict]:
    items = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def load_from_huggingface(dataset_id: str, split: str = "test") -> list[dict]:
    from datasets import load_dataset
    ds = load_dataset(dataset_id, split=split)
    return [dict(row) for row in ds]


def save_to_jsonl(items: list[dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def load_dataset_for_tier(config: Config) -> list[EvalTask]:
    """Load dataset based on tier and online/offline mode."""
    tier_cfg = config.tier_config
    source = tier_cfg.get("dataset_source", "auto")
    local_path = PROJECT_ROOT / tier_cfg.get("local_path", "")
    hf_id = tier_cfg.get("huggingface_id", "")

    raw_items: list[dict] = []

    if source == "local" or config.offline:
        if not local_path.exists():
            raise FileNotFoundError(
                f"Local dataset not found: {local_path}\n"
                f"Run 'python scripts/export_dataset.py --tier {config.tier}' first."
            )
        raw_items = load_from_jsonl(local_path)
    elif source == "auto":
        if local_path.exists():
            raw_items = load_from_jsonl(local_path)
        elif hf_id:
            raw_items = load_from_huggingface(hf_id)
            save_to_jsonl(raw_items, local_path)
        else:
            raise ValueError(f"No dataset source for tier '{config.tier}'")
    elif source == "huggingface":
        raw_items = load_from_huggingface(hf_id)
    else:
        raise ValueError(f"Unknown dataset_source: {source}")

    return [EvalTask.from_swebench(item) for item in raw_items]
