#!/usr/bin/env python3
"""
SWE-bench Lite / Verified 데이터셋을 변별력 있는 부분집합으로 추출.

사용법:
    python scripts/swebench_sampler.py \
        --input data/swebench_lite_origin.jsonl \
        --output data/swebench_lite_subset.jsonl \
        --size 20 \
        --dataset lite \
        --seed 42

    python scripts/swebench_sampler.py \
        --input data/swebench_verified_origin.jsonl \
        --output data/swebench_verified_subset.jsonl \
        --size 20 \
        --dataset verified \
        --seed 42

층화 기준:
  - repo (필수): repo 다양성 확보
  - difficulty (verified only): 난이도 분포 유지
  - patch_size_bucket: small/medium/large 패치 비례 추출
"""

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path


def load_jsonl(path):
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def write_jsonl(path, items):
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def patch_size_bucket(patch_text):
    """patch 라인 수 기준으로 small/medium/large 분류."""
    if not patch_text:
        return "small"
    lines = patch_text.count("\n")
    if lines < 30:
        return "small"
    elif lines < 100:
        return "medium"
    else:
        return "large"


def make_strata_key(item, dataset_type):
    """층(strata)을 정의하는 키 생성."""
    repo = item.get("repo", "unknown")
    bucket = patch_size_bucket(item.get("patch", ""))

    if dataset_type == "verified":
        # Verified는 difficulty 필드가 있음
        difficulty = item.get("difficulty", "unknown")
        return (repo, difficulty, bucket)
    else:
        return (repo, bucket)


def stratified_sample(items, size, dataset_type, seed=42):
    """층화 샘플링: 각 층(strata)에서 비례적으로 추출."""
    rng = random.Random(seed)

    # 층별로 그룹핑
    strata = defaultdict(list)
    for item in items:
        key = make_strata_key(item, dataset_type)
        strata[key].append(item)

    total = len(items)
    selected = []
    leftover_pool = []

    # 1단계: 각 층에서 비례 할당 (반올림 결과 1개 미만이어도 최소 1개 보장 시도)
    for key, group in strata.items():
        proportion = len(group) / total
        target = max(1, round(proportion * size))
        # 그룹 셔플 후 target 개수만큼 추출
        shuffled = group[:]
        rng.shuffle(shuffled)
        selected.extend(shuffled[:target])
        leftover_pool.extend(shuffled[target:])

    # 2단계: 목표 size에 맞게 조정
    if len(selected) > size:
        # 너무 많으면 무작위로 줄임 (단, 각 repo가 최소 1개는 남도록 가능한 보존)
        rng.shuffle(selected)
        # repo별 1개는 우선 보존
        kept_repos = set()
        priority, rest = [], []
        for it in selected:
            repo = it.get("repo")
            if repo not in kept_repos:
                kept_repos.add(repo)
                priority.append(it)
            else:
                rest.append(it)
        # priority + rest를 채워 size 개수 맞춤
        final = priority[:size]
        if len(final) < size:
            final.extend(rest[: size - len(final)])
        selected = final
    elif len(selected) < size:
        # 부족하면 leftover_pool에서 추가
        rng.shuffle(leftover_pool)
        need = size - len(selected)
        selected.extend(leftover_pool[:need])

    return selected


def print_summary(items, dataset_type, label="Sample"):
    print(f"\n=== {label} Summary (n={len(items)}) ===")
    repo_counts = defaultdict(int)
    diff_counts = defaultdict(int)
    bucket_counts = defaultdict(int)
    for it in items:
        repo_counts[it.get("repo", "?")] += 1
        bucket_counts[patch_size_bucket(it.get("patch", ""))] += 1
        if dataset_type == "verified":
            diff_counts[it.get("difficulty", "?")] += 1

    print("\n[Repo 분포]")
    for repo, cnt in sorted(repo_counts.items(), key=lambda x: -x[1]):
        print(f"  {repo:40s} {cnt}")

    print("\n[Patch size 분포]")
    for b in ["small", "medium", "large"]:
        print(f"  {b:8s} {bucket_counts.get(b, 0)}")

    if dataset_type == "verified" and diff_counts:
        print("\n[Difficulty 분포]")
        for d, c in sorted(diff_counts.items()):
            print(f"  {d:25s} {c}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="원본 JSONL 경로")
    ap.add_argument("--output", required=True, help="추출된 JSONL 저장 경로")
    ap.add_argument("--size", type=int, required=True, help="추출할 인스턴스 수")
    ap.add_argument(
        "--dataset",
        choices=["lite", "verified"],
        required=True,
        help="데이터셋 종류 (verified는 difficulty 필드 사용)",
    )
    ap.add_argument("--seed", type=int, default=42, help="재현성용 seed")
    args = ap.parse_args()

    items = load_jsonl(args.input)
    print(f"입력 인스턴스 수: {len(items)}")
    print_summary(items, args.dataset, label="Original")

    if args.size >= len(items):
        print(f"\n[경고] 요청 size({args.size}) >= 전체({len(items)}). 전체를 그대로 출력합니다.")
        sample = items
    else:
        sample = stratified_sample(items, args.size, args.dataset, args.seed)

    print_summary(sample, args.dataset, label="Sampled")

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output, sample)
    print(f"\n저장 완료: {args.output} ({len(sample)} 인스턴스)")

    # instance_id 목록도 별도로 저장 (실행 시 --instance_ids 인자로 사용 가능)
    ids_path = Path(args.output).with_suffix(".ids.txt")
    with open(ids_path, "w") as f:
        for it in sample:
            f.write(it["instance_id"] + "\n")
    print(f"instance_id 목록: {ids_path}")


if __name__ == "__main__":
    main()