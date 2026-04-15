#!/usr/bin/env python3
"""Create synthetic test dataset for pipeline validation."""

import sys
import os
import json
import random
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.config import PROJECT_ROOT

# Synthetic SWE-bench-like instances for testing
INSTANCES = [
    {
        "instance_id": "django__django-16379",
        "repo": "django/django",
        "base_commit": "abc123def456",
        "problem_statement": "FileBasedCache has_key is susceptible to race conditions",
        "hints_text": "",
        "patch": "diff --git a/django/core/cache/backends/filebased.py b/django/core/cache/backends/filebased.py\n--- a/django/core/cache/backends/filebased.py\n+++ b/django/core/cache/backends/filebased.py\n@@ -91,4 +91,8 @@\n-        return os.path.exists(fname)\n+        try:\n+            return os.path.exists(fname)\n+        except FileNotFoundError:\n+            return False",
        "test_patch": "",
        "difficulty": "easy",
        "version": "5.0",
        "FAIL_TO_PASS": "[\"test_has_key_race_condition\"]",
        "PASS_TO_PASS": "[\"test_simple_set_get\", \"test_delete\"]",
    },
    {
        "instance_id": "django__django-16400",
        "repo": "django/django",
        "base_commit": "def789ghi012",
        "problem_statement": "migrate --check should return a non-zero exit code on pending migrations",
        "hints_text": "",
        "patch": "diff --git a/django/core/management/commands/migrate.py ...",
        "test_patch": "",
        "difficulty": "medium",
        "version": "5.0",
        "FAIL_TO_PASS": "[\"test_migrate_check_exit_code\"]",
        "PASS_TO_PASS": "[\"test_migrate_basic\"]",
    },
    {
        "instance_id": "django__django-16527",
        "repo": "django/django",
        "base_commit": "ghi345jkl678",
        "problem_statement": "\"show_save_as_new\" in admin causes a crash when used with inlines",
        "hints_text": "Check the save logic in options.py",
        "patch": "diff --git a/django/contrib/admin/options.py ...",
        "test_patch": "",
        "difficulty": "hard",
        "version": "5.0",
        "FAIL_TO_PASS": "[\"test_save_as_new_with_inlines\"]",
        "PASS_TO_PASS": "[\"test_save_basic\", \"test_inline_basic\"]",
    },
    {
        "instance_id": "django__django-16595",
        "repo": "django/django",
        "base_commit": "jkl901mno234",
        "problem_statement": "Migration optimizer does not reduce multiple AlterField",
        "hints_text": "",
        "patch": "diff --git a/django/db/migrations/optimizer.py ...",
        "test_patch": "",
        "difficulty": "easy",
        "version": "5.0",
        "FAIL_TO_PASS": "[\"test_alter_field_optimization\"]",
        "PASS_TO_PASS": "[\"test_create_model\"]",
    },
    {
        "instance_id": "django__django-16816",
        "repo": "django/django",
        "base_commit": "mno567pqr890",
        "problem_statement": "Error message for invalid model field choices is misleading",
        "hints_text": "",
        "patch": "diff --git a/django/db/models/fields/__init__.py ...",
        "test_patch": "",
        "difficulty": "medium",
        "version": "5.0",
        "FAIL_TO_PASS": "[\"test_invalid_choice_error_message\"]",
        "PASS_TO_PASS": "[\"test_valid_choices\"]",
    },
]


def main():
    data_dir = PROJECT_ROOT / "data"
    data_dir.mkdir(exist_ok=True)

    # Save micro dataset (5 instances for quick testing)
    micro_path = data_dir / "swebench_micro.jsonl"
    with open(micro_path, "w") as f:
        for item in INSTANCES:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"Created test micro dataset: {micro_path} ({len(INSTANCES)} instances)")

    # Also create a mini-like dataset (same data, for testing loader)
    mini_path = data_dir / "swebench_mini.jsonl"
    with open(mini_path, "w") as f:
        for item in INSTANCES:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"Created test mini dataset: {mini_path} ({len(INSTANCES)} instances)")


if __name__ == "__main__":
    main()
