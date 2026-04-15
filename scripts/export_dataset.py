#!/usr/bin/env python3
"""Export dataset for offline use (external -> internal network transfer)."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from pathlib import Path
from rich.console import Console

from src.core.config import PROJECT_ROOT
from src.dataset.loader import load_from_huggingface, save_to_jsonl
from src.dataset.sampler import create_micro_dataset
from src.core.models import EvalTask

console = Console()

TIER_HF_IDS = {
    "mini": "MariusHobbhahn/swe-bench-verified-mini",
    "full": "princeton-nlp/SWE-bench_Verified",
}


@click.command()
@click.option("--tier", type=click.Choice(["micro", "mini", "full"]),
              default="mini", help="Dataset tier to export")
@click.option("--output", default="data", help="Output directory")
def main(tier, output):
    """Export SWE-bench dataset for offline use."""
    output_dir = PROJECT_ROOT / output

    if tier == "micro":
        # First load mini, then sample micro
        console.print("Loading mini dataset from HuggingFace...")
        hf_id = TIER_HF_IDS["mini"]
        raw = load_from_huggingface(hf_id)
        console.print(f"  Loaded {len(raw)} instances")

        # Save mini
        mini_path = output_dir / "swebench_mini.jsonl"
        save_to_jsonl(raw, mini_path)
        console.print(f"  Saved mini: {mini_path}")

        # Sample micro
        tasks = [EvalTask.from_swebench(item) for item in raw]
        micro = create_micro_dataset(tasks, n=10)
        micro_raw = [raw[i] for i, t in enumerate(tasks) if t in micro]

        # Fallback: just take first 10 django items
        if not micro_raw:
            micro_raw = [item for item in raw if "django" in item.get("repo", "").lower()][:10]

        micro_path = output_dir / "swebench_micro.jsonl"
        save_to_jsonl(micro_raw, micro_path)
        console.print(f"  Saved micro: {micro_path} ({len(micro_raw)} instances)")

    elif tier in TIER_HF_IDS:
        hf_id = TIER_HF_IDS[tier]
        console.print(f"Loading {tier} dataset from HuggingFace ({hf_id})...")
        raw = load_from_huggingface(hf_id)
        console.print(f"  Loaded {len(raw)} instances")

        path = output_dir / f"swebench_{tier}.jsonl"
        save_to_jsonl(raw, path)
        console.print(f"  Saved: {path}")

    console.print("[green]Export complete![/green]")
    console.print(f"Transfer the '{output}/' directory to the target environment.")


if __name__ == "__main__":
    main()
