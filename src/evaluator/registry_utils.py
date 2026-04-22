"""Shared Docker registry error classification + retry policy.

Used by:
  - `scripts/check_docker_images.py` (pre-flight manifest inspection)
  - `src/evaluator/docker_evaluator.py` (actual image pull during evaluation)

Keeping this in one place ensures both paths classify the same errors
identically and retry with the same backoff, so the pre-flight script is an
honest proxy for what the real evaluation path will do.
"""

from __future__ import annotations

import random

# Keyword → category, ordered from most specific to generic.
# `rate_limit` precedes `auth` because GHCR's 429 responses can carry
# auth-adjacent phrasing; we want the former to win.
ERROR_CATEGORIES = [
    ("not_found",  ("no such manifest", "not found", "manifest unknown")),
    ("rate_limit", ("toomanyrequests", "too many requests", "rate limit", "429")),
    ("auth",       ("unauthorized", "denied", "forbidden", "401")),
    ("timeout",    ("timeout", "timed out", "deadline", "i/o timeout")),
    ("tls",        ("tls", "certificate", "x509")),
    ("dns",        ("no such host", "dns", "lookup")),
    ("network",    ("connection refused", "connect:",
                    "connection reset", "reset by peer",
                    "broken pipe", "unexpected eof")),
]

# Categories worth retrying — transient in nature.
# Persistent ones (not_found, auth, tls, dns, unknown) fail fast to avoid
# wasting minutes on operations that will never succeed without intervention.
RETRYABLE = frozenset({"rate_limit", "timeout", "network"})


def classify(err: str) -> str:
    """Map a docker/registry error string to a coarse category."""
    el = err.lower()
    for cat, kws in ERROR_CATEGORIES:
        if any(k in el for k in kws):
            return cat
    return "unknown"


def backoff_seconds(category: str, attempt: int) -> float:
    """Exponential backoff with jitter.

    `rate_limit` uses a longer base (GHCR's anonymous window is ~1h and
    partial resets occur within that window, so 15s→30s→60s is a reasonable
    balance between throughput and avoiding further 429s). Other transient
    categories use 2s→4s→8s.
    """
    base = 15 if category == "rate_limit" else 2
    return base * (2 ** attempt) + random.uniform(0, 1)
