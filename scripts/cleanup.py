#!/usr/bin/env python3
"""Clean up Docker resources and temp files from evaluation runs.

Only touches resources owned by this eval harness — never global prune.
On shared servers, `docker container/image prune` would wipe other developers'
stopped containers and dangling build layers. We only delete:
  - /tmp/cae_*        (our sandbox workdirs)
  - cae_*  containers (our named eval containers)
  - ghcr.io/epoch-research/swe-bench.eval.* images (only with confirmation)
"""

import subprocess
import shutil
import glob


def _list_cae_containers() -> list[str]:
    """Return names of containers matching `cae_*` (any state)."""
    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}",
         "--filter", "name=^cae_"],
        capture_output=True, text=True, timeout=10,
    )
    return [n for n in result.stdout.strip().splitlines() if n]


def main():
    print("Coding-Agent-Eval Cleanup\n")

    # 1. Stale temp directories (our workdirs only)
    stale = glob.glob("/tmp/cae_*")
    if stale:
        print(f"Removing {len(stale)} temp directories...")
        for d in stale:
            shutil.rmtree(d, ignore_errors=True)
        print("  Done.")
    else:
        print("No stale temp directories.")

    # 2. Our eval containers only (cae_* named) — never global prune
    print("\nRemoving cae_* eval containers...")
    cae_containers = _list_cae_containers()
    if cae_containers:
        for name in cae_containers:
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True, timeout=30,
            )
        print(f"  Removed {len(cae_containers)} container(s).")
    else:
        print("  None found.")

    # 3. SWE-bench images (interactive — same as before)
    result = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}",
         "--filter", "reference=ghcr.io/epoch-research/swe-bench.eval.*"],
        capture_output=True, text=True, timeout=10,
    )
    images = [l for l in result.stdout.strip().split("\n") if l]

    if images:
        print(f"\nSWE-bench images ({len(images)}):")
        for img in images:
            print(f"  {img}")

        answer = input(f"\nRemove all {len(images)} SWE-bench images? [y/N] ")
        if answer.lower() == "y":
            for img in images:
                name = img.split("\t")[0]
                subprocess.run(
                    ["docker", "rmi", name],
                    capture_output=True, timeout=60,
                )
            print("  Images removed.")
        else:
            print("  Skipped.")
    else:
        print("\nNo SWE-bench images found.")

    # NOTE: We intentionally do NOT run `docker image prune` or
    # `docker container prune` here. Those are global and would delete
    # other developers' resources on a shared server.

    print("\nCleanup complete.")


if __name__ == "__main__":
    main()
