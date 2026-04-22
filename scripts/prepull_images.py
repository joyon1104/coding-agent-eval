#!/usr/bin/env python3
"""Pre-pull SWE-bench Docker images for given dataset JSONL file(s).

Resumable by design:
  - Reuses `pull_image()` from docker_evaluator, which checks
    `image_exists_locally()` before pulling — already-cached images are skipped.
  - Built-in classification + exponential backoff for transient network
    failures (rate_limit / timeout / network). Persistent failures
    (not_found / auth / tls / dns) bail fast so retries don't waste time.
  - On partial completion, just re-run with the same arguments; completed
    images are skipped on the next pass.

Typical usage:
    python scripts/prepull_images.py \\
        --dataset data/swebench_lite_small.jsonl \\
        --dataset data/swebench_verified_small.jsonl
"""

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from rich.console import Console

from src.core.config import PROJECT_ROOT
from src.evaluator.docker_evaluator import (
    get_image_name,
    image_exists_locally,
    pull_image,
)

console = Console()


@click.command()
@click.option("--dataset", "datasets", multiple=True, required=True,
              help="JSONL file(s) to pre-pull images for. Repeatable.")
@click.option("--max-retries", default=3, show_default=True,
              help="Retries per image for transient failures")
@click.option("--timeout", default=1200, show_default=True,
              help="Timeout per pull attempt (seconds). Large images need more.")
@click.option("--dry-run", is_flag=True,
              help="List what would be pulled without pulling")
def main(datasets, max_retries, timeout, dry_run):
    """Pre-pull SWE-bench Docker images for specified dataset JSONL file(s)."""
    # Enable pull_image's internal INFO/WARNING logs to show on stdout
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # 1. Load unique instance_ids across all datasets
    console.print("[bold]Datasets[/bold]")
    all_ids: list[str] = []
    for path_str in datasets:
        p = Path(path_str) if Path(path_str).is_absolute() else PROJECT_ROOT / path_str
        if not p.exists():
            console.print(f"[red]Not found: {p}[/red]")
            sys.exit(1)
        ids = [json.loads(l)["instance_id"] for l in p.open() if l.strip()]
        all_ids.extend(ids)
        console.print(f"  {p.relative_to(PROJECT_ROOT)}: {len(ids)} instances")

    # Preserve first-seen order while deduping
    seen: set[str] = set()
    unique: list[str] = []
    for iid in all_ids:
        if iid not in seen:
            seen.add(iid)
            unique.append(iid)

    # 2. Partition into already-local vs needs-pull
    already_local: list[str] = []
    need_pull: list[str] = []
    for iid in unique:
        if image_exists_locally(get_image_name(iid)):
            already_local.append(iid)
        else:
            need_pull.append(iid)

    console.print(f"\n[bold]Status[/bold]")
    console.print(f"  unique instances: {len(unique)}")
    console.print(f"  already cached:   [green]{len(already_local)}[/green]")
    console.print(f"  need to pull:     [yellow]{len(need_pull)}[/yellow]")

    if dry_run:
        console.print("\n[yellow]--dry-run: no pulls will be performed[/yellow]")
        for iid in need_pull:
            console.print(f"  would pull: {get_image_name(iid)}")
        return

    if not need_pull:
        console.print("\n[bold green]All images already cached locally. Nothing to do.[/bold green]")
        return

    # 3. Pull each sequentially. Docker daemon serializes concurrent pulls
    # internally anyway, and sequential output stays readable for debugging.
    console.print(f"\n[bold]Pulling {len(need_pull)} images[/bold] "
                  f"(max_retries={max_retries}, timeout_per_try={timeout}s)")
    pulled: list[str] = []
    failed: list[str] = []

    for i, iid in enumerate(need_pull, 1):
        console.print(f"\n[bold cyan][{i}/{len(need_pull)}] {iid}[/bold cyan]")
        ok = pull_image(iid, max_retries=max_retries, timeout_per_try=timeout)
        (pulled if ok else failed).append(iid)

    # 4. Final summary
    console.print(f"\n{'=' * 60}")
    console.print(f"[bold]Summary[/bold]")
    console.print(f"  pulled this run:   [green]{len(pulled)}[/green] / {len(need_pull)}")
    console.print(f"  failed this run:   [red]{len(failed)}[/red] / {len(need_pull)}")
    console.print(f"  total now cached:  [green]{len(already_local) + len(pulled)}[/green] / {len(unique)}")

    if failed:
        console.print(f"\n[yellow]Failed instances (re-run to resume):[/yellow]")
        for iid in failed:
            console.print(f"  - {iid}")
        console.print(f"\n[dim]Tip: rerun the same command — successful pulls are skipped.[/dim]")
        sys.exit(1)

    console.print(f"\n[bold green]All images for the specified datasets are now cached.[/bold green]")


if __name__ == "__main__":
    main()
