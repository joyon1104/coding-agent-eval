#!/usr/bin/env python3
"""Pre-flight check: Docker image availability for a tier.

Runs `docker manifest inspect` on every instance's SWE-bench image in parallel.
Metadata-only — no actual image download. Useful to verify registry/network/auth
paths before running a full evaluation, especially on restricted networks.
"""

import json
import os
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from rich.console import Console

from src.core.config import PROJECT_ROOT
from src.evaluator.docker_evaluator import get_image_name

console = Console()

# Keyword → category, ordered from most specific to generic
_ERROR_CATEGORIES = [
    ("not_found", ("no such manifest", "not found", "manifest unknown")),
    ("auth",      ("unauthorized", "denied", "forbidden")),
    ("timeout",   ("timeout", "timed out", "deadline")),
    ("tls",       ("tls", "certificate", "x509")),
    ("dns",       ("no such host", "dns", "lookup")),
    ("network",   ("connection refused", "connect:")),
]


def _classify(err: str) -> str:
    el = err.lower()
    for cat, kws in _ERROR_CATEGORIES:
        if any(k in el for k in kws):
            return cat
    return "unknown"


def check_one(instance_id: str, timeout: int = 30) -> dict:
    """Check a single image's manifest. Metadata-only, no pull."""
    image = get_image_name(instance_id)
    t0 = time.time()
    try:
        r = subprocess.run(
            ["docker", "manifest", "inspect", image],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode == 0:
            return {
                "instance_id": instance_id, "image": image,
                "ok": True, "elapsed": round(time.time() - t0, 2),
            }
        err = (r.stderr or r.stdout).strip()
        return {
            "instance_id": instance_id, "image": image, "ok": False,
            "category": _classify(err), "error": err[:300],
            "elapsed": round(time.time() - t0, 2),
        }
    except subprocess.TimeoutExpired:
        return {
            "instance_id": instance_id, "image": image, "ok": False,
            "category": "timeout",
            "error": f"hard timeout after {timeout}s",
            "elapsed": timeout,
        }


@click.command()
@click.option("--tier", default="verified",
              type=click.Choice(["local", "lite", "verified", "full", "multi"]),
              help="Tier whose dataset JSONL to read")
@click.option("--concurrency", default=8, show_default=True,
              help="Parallel manifest checks")
@click.option("--timeout", default=30, show_default=True,
              help="Per-image timeout (seconds)")
@click.option("--output", default=None,
              help="Report JSON path (default: results/image_availability_<tier>.json)")
@click.option("--only-failing", is_flag=True,
              help="Re-check only the instances that failed in a previous report")
@click.option("--input", "input_path", default=None,
              help="Previous report JSON (used with --only-failing)")
def main(tier, concurrency, timeout, output, only_failing, input_path):
    """Check Docker image availability for every instance in a tier."""
    ds_path = PROJECT_ROOT / "data" / f"swebench_{tier}.jsonl"
    if not ds_path.exists():
        console.print(f"[red]Dataset not found: {ds_path}[/red]")
        sys.exit(1)

    # Determine instance list
    if only_failing:
        prev = Path(input_path) if input_path else (
            PROJECT_ROOT / "results" / f"image_availability_{tier}.json")
        if not prev.exists():
            console.print(f"[red]Previous report not found: {prev}[/red]")
            sys.exit(1)
        prev_data = json.loads(prev.read_text())
        instances = [r["instance_id"] for r in prev_data if not r.get("ok")]
        console.print(f"Re-checking {len(instances)} previously-failed instances from {prev.name}")
    else:
        instances = [json.loads(l)["instance_id"] for l in ds_path.open()]
        console.print(f"Checking {len(instances)} images from tier=[bold]{tier}[/bold] "
                      f"(concurrency={concurrency}, timeout={timeout}s)")

    if not instances:
        console.print("[yellow]Nothing to check.[/yellow]")
        return

    results = []
    t_start = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(check_one, iid, timeout): iid for iid in instances}
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            results.append(r)
            if r["ok"]:
                mark = "[green]OK  [/green]"
            else:
                mark = f"[red]FAIL[{r['category']}][/red]"
            console.print(f"  [{i:>4}/{len(instances)}] {mark} {r['instance_id']}")

    elapsed = time.time() - t_start

    # Summary
    ok = sum(1 for r in results if r["ok"])
    console.print(f"\n[bold]Summary[/bold]: {ok}/{len(results)} OK — {elapsed:.1f}s total")
    cats = Counter(r["category"] for r in results if not r["ok"])
    for cat, n in cats.most_common():
        console.print(f"  [red]{cat:<12}[/red]: {n}")

    # Save report (sorted for stable diffs)
    out_path = Path(output) if output else (
        PROJECT_ROOT / "results" / f"image_availability_{tier}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results_sorted = sorted(results, key=lambda r: r["instance_id"])
    out_path.write_text(json.dumps(results_sorted, indent=2, ensure_ascii=False))
    console.print(f"\nReport: [bold]{out_path}[/bold]")

    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    main()
