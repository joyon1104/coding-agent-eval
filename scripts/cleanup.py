#!/usr/bin/env python3
"""Clean up Docker resources and temp files from evaluation runs."""

import subprocess
import shutil
import glob
from pathlib import Path

def main():
    print("Coding-Agent-Eval Cleanup\n")

    # 1. Stale temp directories
    stale = glob.glob("/tmp/cape_*")
    if stale:
        print(f"Removing {len(stale)} temp directories...")
        for d in stale:
            shutil.rmtree(d, ignore_errors=True)
        print("  Done.")
    else:
        print("No stale temp directories.")

    # 2. Stopped containers
    print("\nRemoving stopped containers...")
    result = subprocess.run(
        ["docker", "container", "prune", "-f"],
        capture_output=True, text=True, timeout=30,
    )
    print(f"  {result.stdout.strip()}")

    # 3. SWE-bench images
    result = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}",
         "--filter", "reference=ghcr.io/epoch-research/*"],
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

    # 4. Dangling images
    print("\nRemoving dangling images...")
    result = subprocess.run(
        ["docker", "image", "prune", "-f"],
        capture_output=True, text=True, timeout=60,
    )
    print(f"  {result.stdout.strip()}")

    print("\nCleanup complete.")


if __name__ == "__main__":
    main()
