"""Language dispatch: map (task, tier) → LanguageProfile.

Rules:
  - tier != "multi"  → always PythonProfile (zero impact on Lite/Verified)
  - tier == "multi"  → look up REPO_LANGUAGE; raise ValueError if repo is unknown

Adding a new language:
  1. Create src/evaluator/languages/<lang>.py implementing LanguageProfile
  2. Add each repo → Profile entry in REPO_LANGUAGE below
  3. Run: python scripts/run_eval.py --tier multi --sample-size 1 to verify
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.evaluator.languages.profile import LanguageProfile
from src.evaluator.languages.python import PythonProfile
from src.evaluator.languages.java import JavaProfile
from src.evaluator.languages.cpp import CppProfile
from src.evaluator.languages.c import CProfile, JqProfile, MicropythonProfile, RedisProfile, ValkeyProfile
from src.evaluator.languages.go import GoProfile
from src.evaluator.languages.rust import RustProfile
from src.evaluator.languages.ruby import RubyProfile, RubocopProfile
from src.evaluator.languages.php import PhpProfile
from src.evaluator.languages.javascript import JavaScriptProfile

if TYPE_CHECKING:
    from src.core.models import EvalTask

# repo (owner/name) → LanguageProfile class
# All 41 repos from SWE-bench Multilingual (Princeton, 300 instances)
REPO_LANGUAGE: dict[str, type[LanguageProfile]] = {
    # ── Java ──────────────────────────────────────────────────────────────
    "apache/druid": JavaProfile,
    "apache/lucene": JavaProfile,
    "google/gson": JavaProfile,
    "javaparser/javaparser": JavaProfile,
    "projectlombok/lombok": JavaProfile,
    "reactivex/rxjava": JavaProfile,

    # ── C++ ───────────────────────────────────────────────────────────────
    "fmtlib/fmt": CppProfile,
    "nlohmann/json": CppProfile,

    # ── C ─────────────────────────────────────────────────────────────────
    "jqlang/jq": JqProfile,
    "micropython/micropython": MicropythonProfile,
    "redis/redis": RedisProfile,
    "valkey-io/valkey": ValkeyProfile,

    # ── Go ────────────────────────────────────────────────────────────────
    "caddyserver/caddy": GoProfile,
    "gin-gonic/gin": GoProfile,
    "gohugoio/hugo": GoProfile,
    "hashicorp/terraform": GoProfile,
    "prometheus/prometheus": GoProfile,

    # ── Rust ──────────────────────────────────────────────────────────────
    "astral-sh/ruff": RustProfile,
    "burntsushi/ripgrep": RustProfile,
    "nushell/nushell": RustProfile,
    "sharkdp/bat": RustProfile,
    "tokio-rs/axum": RustProfile,
    "tokio-rs/tokio": RustProfile,
    "uutils/coreutils": RustProfile,

    # ── Ruby ──────────────────────────────────────────────────────────────
    "faker-ruby/faker": RubyProfile,
    "fastlane/fastlane": RubyProfile,
    "fluent/fluentd": RubyProfile,
    "jekyll/jekyll": RubyProfile,
    "jordansissel/fpm": RubyProfile,
    "rubocop/rubocop": RubocopProfile,

    # ── PHP ───────────────────────────────────────────────────────────────
    "briannesbitt/carbon": PhpProfile,
    "laravel/framework": PhpProfile,
    "php-cs-fixer/php-cs-fixer": PhpProfile,
    "phpoffice/phpspreadsheet": PhpProfile,

    # ── JavaScript / TypeScript ───────────────────────────────────────────
    "axios/axios": JavaScriptProfile,
    "babel/babel": JavaScriptProfile,
    "facebook/docusaurus": JavaScriptProfile,
    "immutable-js/immutable-js": JavaScriptProfile,
    "mrdoob/three.js": JavaScriptProfile,
    "preactjs/preact": JavaScriptProfile,
    "vuejs/core": JavaScriptProfile,
}


def get_profile(task: "EvalTask", tier: str) -> LanguageProfile:
    """Return the LanguageProfile for (task, tier).

    Tier other than "multi" always returns PythonProfile — this is the
    zero-impact guarantee: Lite/Verified/full evals are never affected.
    """
    if tier != "multi":
        return PythonProfile()

    cls = REPO_LANGUAGE.get(task.repo)
    if cls is None:
        raise ValueError(
            f"No language profile registered for repo={task.repo!r} (tier=multi). "
            f"Add a mapping to src/evaluator/languages/dispatch.py:REPO_LANGUAGE."
        )
    return cls()
