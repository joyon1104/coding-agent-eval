#!/usr/bin/env python3
"""Pick the N smallest instances from a tier (image size + text content).

Selection:
  - Compound sort by (compressed image size ASC, text bytes ASC) — smaller first.
  - `--per-repo-max M` caps how many instances can come from the same repo,
    which trades a tiny bit of smallness for repo diversity. M=1 guarantees
    each picked instance comes from a distinct repo.
  - Image sizes are cached at data/.image_size_cache.json so repeated runs
    (e.g. to tweak --per-repo-max or --n) don't re-query registries (GHCR/Docker Hub).

Registry:
  - tier in {lite, verified, full} → GHCR (ghcr.io/epoch-research)
  - tier=multi → Docker Hub (docker.io/swebench) with __→_1776_ instance_id transform

Output: data/swebench_<tier>_small.jsonl (overridable via --output).
The original dataset file is never modified.
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

_TEXT_FIELDS = ("problem_statement", "hints_text", "patch", "test_patch")
_CACHE_PATH = PROJECT_ROOT / "data" / ".image_size_cache.json"


def _load_cache() -> dict[str, int]:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict[str, int]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, indent=2))


def fetch_image_size(full_image_ref: str, retries: int = 2) -> int | None:
    """Total compressed layer bytes via `docker manifest inspect`.

    full_image_ref: fully-qualified image reference including registry host
      e.g. "docker.io/swebench/sweb.eval.x86_64.apache_1776_druid-13704"
           "ghcr.io/epoch-research/swe-bench.eval.x86_64.django__django-1234"
    Uses local docker credentials (~/.docker/config.json), so `docker login`
    is respected and Docker Hub rate limits are avoided.
    """
    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                ["docker", "manifest", "inspect", full_image_ref],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return None
            m = json.loads(result.stdout)
            return sum(l.get("size", 0) for l in m.get("layers", []))
        except Exception:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            return None
    return None


def text_size(row: dict) -> int:
    return sum(len(row.get(f, "") or "") for f in _TEXT_FIELDS)


@click.command()
@click.option("--tier", required=True,
              type=click.Choice(["lite", "verified", "full", "multi"]),
              help="Source tier JSONL to pick from")
@click.option("--n", required=True, type=int,
              help="Number of smallest instances to pick")
@click.option("--output", default=None,
              help="Output JSONL path (default: data/swebench_<tier>_small.jsonl)")
@click.option("--input", "input_path", default=None,
              help="Source JSONL override (default: data/swebench_<tier>.jsonl)")
@click.option("--concurrency", default=8, show_default=True,
              help="Parallel manifest queries")
@click.option("--per-repo-max", default=None, type=int,
              help="Max instances per repo (e.g. 1 for full diversity). No cap if omitted.")
def main(tier, n, output, input_path, concurrency, per_repo_max):
    """Pick N smallest instances ranked by (image_size, text_size)."""
    src = Path(input_path) if input_path else PROJECT_ROOT / "data" / f"swebench_{tier}.jsonl"
    if not src.exists():
        console.print(f"[red]Source dataset not found: {src}[/red]")
        sys.exit(1)

    rows = [json.loads(l) for l in src.open()]
    console.print(f"Loaded [bold]{len(rows)}[/bold] instances from tier=[bold]{tier}[/bold]")

    cache = _load_cache()
    need_query = [r for r in rows if r["instance_id"] not in cache]
    console.print(
        f"Cached sizes: {len(rows) - len(need_query)}/{len(rows)}  "
        f"→ querying {len(need_query)} new (concurrency={concurrency}, tier={tier})..."
    )

    sizes: dict[str, int | None] = {r["instance_id"]: cache.get(r["instance_id"]) for r in rows}

    if need_query:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = {}
            for r in need_query:
                if tier == "multi":
                    img_base = r["instance_id"].replace("__", "_1776_")
                    full_ref = f"docker.io/swebench/sweb.eval.x86_64.{img_base}"
                else:
                    full_ref = f"ghcr.io/epoch-research/swe-bench.eval.x86_64.{r['instance_id']}"

                fut = ex.submit(fetch_image_size, full_ref)
                futures[fut] = r["instance_id"]

            done = 0
            for fut in as_completed(futures):
                iid = futures[fut]
                sz = fut.result()
                sizes[iid] = sz
                if sz is not None:
                    cache[iid] = sz
                done += 1
                if done % 50 == 0 or done == len(need_query):
                    console.print(f"  [{done}/{len(need_query)}] queried")
        _save_cache(cache)
        console.print(f"Queried in {time.time() - t0:.1f}s (cache now has {len(cache)} entries)")

    # Compound sort: image_size ASC (primary), text_size ASC (tiebreaker).
    # Instances whose manifest is unreachable are skipped — if we can't fetch
    # a manifest now, the actual pull is unlikely to succeed either.
    ranked = []
    skipped = 0
    for r in rows:
        img = sizes.get(r["instance_id"])
        if img is None:
            skipped += 1
            continue
        ranked.append((img, text_size(r), r))
    ranked.sort(key=lambda x: (x[0], x[1]))

    if skipped:
        console.print(f"[yellow]Skipped {skipped} instances (manifest unreachable)[/yellow]")

    # Apply per-repo cap: walk the size-sorted list, keep track of how many
    # we've taken from each repo, skip anything past the cap. Preserves
    # "smallest first" while enforcing diversity.
    # If the initial cap yields fewer than n instances, relax it incrementally
    # (per_repo_max → per_repo_max+1 → ...) until we reach n or exhaust all
    # available instances.
    if per_repo_max is not None and per_repo_max > 0:
        total_available = len(ranked)
        effective_cap = per_repo_max
        while True:
            per_repo_count: Counter[str] = Counter()
            capped = []
            for img, txt, r in ranked:
                if per_repo_count[r["repo"]] >= effective_cap:
                    continue
                capped.append((img, txt, r))
                per_repo_count[r["repo"]] += 1
            if len(capped) >= n or len(capped) == total_available:
                ranked = capped
                if effective_cap > per_repo_max:
                    console.print(
                        f"[dim]per-repo-max relaxed {per_repo_max}→{effective_cap} "
                        f"to reach {len(ranked)} eligible across {len(per_repo_count)} repos[/dim]"
                    )
                else:
                    console.print(
                        f"[dim]per-repo-max={effective_cap} → {len(ranked)} eligible "
                        f"across {len(per_repo_count)} repos[/dim]"
                    )
                break
            effective_cap += 1

    if len(ranked) < n:
        console.print(f"[yellow]Only {len(ranked)} available; requested {n}[/yellow]")
        n = len(ranked)

    picked = ranked[:n]
    out = Path(output) if output else PROJECT_ROOT / "data" / f"swebench_{tier}_small.jsonl"
    with open(out, "w") as f:
        for _, _, r in picked:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Summary
    console.print(f"\n[bold green]Picked {len(picked)} smallest from {tier}[/bold green]  →  [bold]{out}[/bold]")

    repo_counts = Counter(r["repo"] for _, _, r in picked)
    console.print(f"\n[bold]Repo distribution[/bold] ({len(repo_counts)} distinct):")
    for repo, cnt in repo_counts.most_common():
        console.print(f"  {repo:<35} × {cnt}")

    console.print(f"\n[bold]Per-instance detail[/bold] (smallest first):")
    total_img = 0
    for img, txt, r in picked:
        total_img += img
        console.print(
            f"  {r['instance_id']:<42}  "
            f"img={img / (1024**3):5.2f}GB  text={txt / 1024:6.1f}KB  "
            f"repo={r['repo']}"
        )
    console.print(f"\n[bold]Totals[/bold]: image disk ≈ {total_img / (1024**3):.1f}GB")


if __name__ == "__main__":
    main()
