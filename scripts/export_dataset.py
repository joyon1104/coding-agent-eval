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

console = Console()

TIER_HF_IDS = {
    "lite": "princeton-nlp/SWE-bench_Lite",
    "verified": "princeton-nlp/SWE-bench_Verified",
    "full": "princeton-nlp/SWE-bench",
    "multi": "SWE-bench/SWE-bench_Multilingual",
}


@click.command()
@click.option("--tier", type=click.Choice(list(TIER_HF_IDS.keys())),
              default="verified", help="Dataset tier to export")
@click.option("--output", default="data", help="Output directory")
def main(tier, output):
    """Export SWE-bench dataset for offline use."""
    output_dir = PROJECT_ROOT / output

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
