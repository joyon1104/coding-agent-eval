# Task: Corporate-network support for the evaluation pipeline

## Background

The harness is developed and validated outside the corporate network, where
PyPI / npm / apt / Docker registries and HTTPS endpoints are reachable directly.
Inside the corporate network, two classes of failure occur silently today:

1. **TLS handshake fails** because outbound traffic is intercepted by the
   corporate proxy, which presents a certificate signed by an internal CA that
   is not present in default trust stores (Python `requests`, Node, Ruby,
   Cargo, …).
2. **Package managers (pip / npm / apt / gem / cargo / …) can't reach
   external registries** because outbound is restricted to the internal mirror.

Where these manifest in the pipeline:

| Stage | What fails today |
|---|---|
| Step 1 — agent execution | Agent runs `pip install` / `npm install` while exploring or verifying its patch and the command errors out; agent reasoning is misled or stalls |
| Step 2 — `docker pull` | TLS verify error on GHCR / Docker Hub (Docker daemon level — out of scope for the harness itself) |
| Step 2 — container test run | Any test that fetches a package fails; some `post_patch_hook` recompilation steps fail |

The codebase currently injects **no** proxy / mirror / CA env var into either
the agent subprocess or the Docker container. The
`config/environments/common.yaml::network.proxy` setting exists but is only
applied to the agent subprocess (`HTTPS_PROXY` only), and only when explicitly
populated. There is no mechanism for the container side.

---

## Goals

### Goal 1 — Opt-in corporate mode via a single CLI flag

Provide one switch the user passes when running inside the corporate network.
Without the switch, behavior is unchanged bit-for-bit. With it, the harness
loads the corporate variables from `.env` and applies them at every relevant
injection point.

### Goal 2 — `.env` is the single source of truth

All site-specific values (proxy URL, internal CA path, mirror URLs per
language) live in `.env`. Switching sites means swapping `.env`, never editing
Python.

### Goal 3 — Variables reach host AND container AND language tooling

The same logical settings must propagate to:
- the agent subprocess on the host (Step 1)
- the Docker container's environment (Step 2 — via `-e` and `-v`)
- language-specific config files inside the container that don't honor env
  vars (Maven `settings.xml`, Cargo `config.toml`, npm `.npmrc`, etc.)

### Goal 4 — Tier coverage: lite, verified, multi

- `lite` / `verified` (Python only): proxy + CA + pip + apt suffice.
- `multi` (9 languages): same as above, plus per-language registry variables
  for JS / Java / Go / Ruby / PHP / Rust. C and C++ are covered by the apt
  mirror.

---

## Non-goals

- Configuring the Docker daemon's own proxy. That belongs to
  `/etc/docker/daemon.json` or `~/.docker/config.json` on the host — the
  harness can't and shouldn't manage it. Documented as a prerequisite only.
- Auto-discovering corporate settings (e.g., from `/etc/environment` or
  Active Directory). The user populates `.env` explicitly.
- Supporting registries beyond the package managers listed below.
- Java keystore conversion (PEM → JKS) — see Open Question Q4.

---

## Requirements

### R1 — CLI flag

A single `--corp` flag is added to:
- `scripts/run_eval.py`
- `scripts/run_docker_eval.py`
- `scripts/prepull_images.py`

Semantics:
- **Absent**: current behavior, no environment injection, no CORP_* required.
- **Present**: harness enters corporate mode and consumes the CORP_* variables
  defined below.

### R2 — Step 1 (agent subprocess) gets corporate env

When `--corp` is on, the agent subprocess inherits the full set of proxy /
mirror / CA env vars so any `pip install`, `npm install`, `git fetch`, etc.
the agent runs resolves against the corporate mirror.

### R3 — Step 2 (Docker container) gets corporate env

When `--corp` is on, the `docker run` command extends to:
- `-e <KEY>=<VAL>` for every active variable
- `-v <CORP_CA_BUNDLE_PATH>:/etc/ssl/corp-ca.pem:ro` mounting the host CA
  bundle read-only, and the in-container `*_CA_*` env vars point at
  `/etc/ssl/corp-ca.pem`

### R4 — Language config bootstrap (multi tier)

For tools that don't fully honor env vars, the harness writes a minimal
configuration file into the container right after startup:

| Tool | File written |
|---|---|
| Maven / Gradle | `~/.m2/settings.xml` with `<mirror>` pointing at `CORP_MAVEN_MIRROR_URL` |
| Cargo | `~/.cargo/config.toml` with the mirror registry |
| npm (if `NPM_CONFIG_REGISTRY` alone isn't enough — e.g. private auth) | `~/.npmrc` |
| Composer | `composer config -g repo.packagist composer …` (CLI call) |
| apt | rewrite `/etc/apt/sources.list` to `CORP_APT_MIRROR_URL` |

This is encapsulated in an optional `pre_test_hook(container_id, corp)` method
on `LanguageProfile`. Default implementation is a no-op so non-corp behavior
stays identical.

### R5 — Tier scoping

| Variable group | lite | verified | multi |
|---|:---:|:---:|:---:|
| Common (proxy + CA) | required | required | required |
| Python (pip) | required | required | required (Python instances) |
| apt mirror | optional | optional | optional |
| npm / Maven / Go / Ruby / PHP / Cargo | not used | not used | optional per language |

"Required" means the harness errors out if missing when `--corp` is on
(see R8). "Optional" means absence is allowed and the corresponding tool just
keeps its default behavior.

### R6 — Metadata captures corp mode

`results/runs/<id>/metadata.json` records `"corp_mode": true|false` so a
report consumer (and the dashboard) can tell at a glance whether a run used
corporate settings. Useful when comparing patch quality across sites.

### R7 — No leakage to non-corp runs

Without `--corp`, the harness must not pass `HTTP_PROXY` / `HTTPS_PROXY` /
any registry variable to the container, even if those vars happen to be set
in the host shell. Current zero-injection default is preserved exactly so
existing benchmark numbers remain reproducible.

### R8 — Fail fast on misconfiguration

When `--corp` is on, before pulling any images or starting any tasks, the
harness validates that the *required* variables (R5) are non-empty and that
`CORP_CA_BUNDLE_PATH` points to a readable file. On failure, exit with a
single clear line:

```
corp mode is on but the following are missing or invalid:
  - CORP_CA_BUNDLE_PATH (file not found: /tmp/typo.pem)
  - PIP_INDEX_URL (unset)
```

---

## Environment variable schema (.env)

### Common — always required when `--corp` is on

| Variable | Purpose |
|---|---|
| `HTTPS_PROXY` | Outbound HTTPS proxy URL |
| `HTTP_PROXY` | Outbound HTTP proxy URL |
| `NO_PROXY` | Comma-separated hosts that bypass the proxy |
| `CORP_CA_BUNDLE_PATH` | Host path to the corporate CA bundle (PEM). Fanned out to standard env vars per tool, and volume-mounted into containers. |

#### CORP_CA_BUNDLE_PATH fan-out

Single user-facing variable; harness expands it to all the language-specific
forms that tools recognize natively:

| Recipient | Standard env var the harness sets |
|---|---|
| Python (`requests`, `pip`) | `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE` |
| curl | `CURL_CA_BUNDLE` |
| git | `GIT_SSL_CAINFO` |
| Node / npm | `NODE_EXTRA_CA_CERTS` |
| Ruby / bundler | `BUNDLE_SSL_CA_CERT` |
| Rust / cargo | `CARGO_HTTP_CAINFO` |
| OpenSSL CLI | `SSL_CERT_FILE` (shared with Python) |

### Python — required when tier is lite / verified / multi-Python

| Variable | Purpose |
|---|---|
| `PIP_INDEX_URL` | Primary pip index |
| `PIP_EXTRA_INDEX_URL` | (optional) secondary index |
| `PIP_TRUSTED_HOST` | Hostname to mark as trusted (skips TLS warnings for mirror) |

### OS package — optional

| Variable | Purpose |
|---|---|
| `CORP_APT_MIRROR_URL` | If set, container's `/etc/apt/sources.list` is rewritten to point here |

### Multi tier — optional per language

| Language | Variable | Purpose / target tool |
|---|---|---|
| JavaScript | `NPM_CONFIG_REGISTRY` | npm / yarn registry |
| Java | `CORP_MAVEN_MIRROR_URL` | Maven & Gradle; injected into `~/.m2/settings.xml` |
| Go | `GOPROXY` | Go module proxy (standard) |
| Go | `GOSUMDB` | Sumdb (set to `off` or internal sumdb URL) |
| Go | `GOPRIVATE` | Hosts to skip checksum (`*.corp`, etc.) |
| Ruby | `CORP_GEM_SOURCE` | RubyGems mirror; translated to `BUNDLE_MIRROR__*` |
| PHP | `CORP_COMPOSER_REPO_URL` | Composer packagist mirror |
| Rust | `CORP_CARGO_REGISTRY_URL` | crates.io mirror; written to `~/.cargo/config.toml` |

### Naming convention

- **Standard env var names** (`HTTPS_PROXY`, `PIP_INDEX_URL`, `GOPROXY`,
  `NPM_CONFIG_REGISTRY`) are used when the tool already honors them — passing
  them through unmodified.
- **`CORP_*` prefix** is used when the tool needs harness-side translation
  (config-file generation, env-var fan-out, mounting) — making it obvious
  which values are "input only" vs "passed through".

---

## Application-point matrix

Where each variable surfaces:

| Variable | Step 1 host env | Step 2 `docker run -e` | Step 2 `docker run -v` | Container config file |
|---|:---:|:---:|:---:|:---:|
| `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` | ✓ | ✓ | — | — |
| `CORP_CA_BUNDLE_PATH` (fan-out) | ✓ | ✓ (as `REQUESTS_CA_BUNDLE` etc. pointing at mount) | ✓ | — |
| `PIP_INDEX_URL`, `PIP_TRUSTED_HOST` | ✓ | ✓ | — | — |
| `CORP_APT_MIRROR_URL` | — | — | — | `/etc/apt/sources.list` |
| `NPM_CONFIG_REGISTRY` | ✓ | ✓ | — | — |
| `CORP_MAVEN_MIRROR_URL` | — | — | — | `~/.m2/settings.xml` |
| `GOPROXY`, `GOSUMDB`, `GOPRIVATE` | ✓ | ✓ | — | — |
| `CORP_GEM_SOURCE` | ✓ (as `BUNDLE_MIRROR__*`) | ✓ (same) | — | — |
| `CORP_COMPOSER_REPO_URL` | — | — | — | `composer config` CLI |
| `CORP_CARGO_REGISTRY_URL` | ✓ (as `CARGO_REGISTRIES_*`) | ✓ | — | `~/.cargo/config.toml` |

Empty cell means the variable doesn't apply to that injection point.

---

## Suggested implementation areas

### `src/core/corp_env.py` (new)

Owns parsing, validation and fan-out. Public surface:

```
class CorpConfig:
    enabled: bool
    common: dict       # HTTPS_PROXY, ...
    ca_bundle: Path | None
    python: dict       # PIP_*
    multi: dict        # per-language
```

- `load(corp_flag: bool, tier: str) -> CorpConfig`
- `validate(cfg) -> list[str]` — returns missing/invalid names; empty list = OK
- `build_host_env() -> dict[str, str]` — fan-out for Step 1
- `build_container_args() -> tuple[list[str], list[tuple[str, str]]]` —
  returns (`-e` pairs, `-v` mounts) for Step 2

### `src/adapters/base.py` (or per-adapter)

When `corp_config.enabled`, merge `build_host_env()` into the agent
subprocess `env` dict. Already exists for `HTTPS_PROXY`; this generalizes it.

### `src/evaluator/docker_evaluator.py`

The `docker run` command currently is:
```
docker run -d --name <name> <image> tail -f /dev/null
```
Becomes (when `corp_config.enabled`):
```
docker run -d --name <name>
  -e HTTPS_PROXY=... -e PIP_INDEX_URL=... -e REQUESTS_CA_BUNDLE=/etc/ssl/corp-ca.pem
  -v /host/ca-bundle.pem:/etc/ssl/corp-ca.pem:ro
  <image> tail -f /dev/null
```
Plus a new call to `profile.pre_test_hook(container_id, corp_config)` right
after the env check, before patch application.

### `src/evaluator/languages/profile.py`

Add to the ABC:
```
def pre_test_hook(self, container_id: str, corp: CorpConfig) -> None:
    """No-op by default; override per language to write settings.xml etc."""
```

Concrete overrides go in `java.py`, `rust.py`, `php.py` (and any subclass
that writes a registry config file).

### CLI surfaces

- `scripts/run_eval.py`: `@click.option("--corp", is_flag=True, ...)`
- `scripts/run_docker_eval.py`: same flag
- `scripts/prepull_images.py`: same flag — pulls happen at Docker daemon
  level, so the flag here is mostly informational, but we accept it for
  consistency.

### Metadata writer

`src/runner/logger.py::save_run_metadata` already accepts arbitrary keys via
`**extra_metadata`. The CLI sets `corp_mode=True/False` and passes it
through. No new code path needed.

---

## Acceptance criteria

### Case 1 — non-corp run (default, current behavior preserved)

Command: `python scripts/run_eval.py --tier lite --agents claude-code --verify`
(no `--corp`).

- No CORP_* variable needs to be set.
- `docker run` command has no `-e` / `-v` flags beyond what exists today.
- Existing tests (`pytest`) pass unchanged.
- `metadata.json` shows `"corp_mode": false`.

### Case 2 — corp run, lite or verified tier

Command: `python scripts/run_eval.py --tier verified --agents claude-code --corp --verify`.

`.env` populated with: `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`,
`CORP_CA_BUNDLE_PATH`, `PIP_INDEX_URL`.

- Agent subprocess sees `HTTPS_PROXY`, `REQUESTS_CA_BUNDLE`, `PIP_INDEX_URL`.
- Container has the host CA bundle mounted at `/etc/ssl/corp-ca.pem`
  and the same env vars set inside.
- `pip install` invoked by either the agent or a `post_patch_hook` resolves
  against the corporate mirror.
- `metadata.json` shows `"corp_mode": true`.

### Case 3 — corp run, multi tier with multiple languages

Command: `python scripts/run_eval.py --tier multi --agents claude-code --corp --verify`.

`.env` additionally has `NPM_CONFIG_REGISTRY`, `CORP_MAVEN_MIRROR_URL`,
`GOPROXY`, `CORP_CARGO_REGISTRY_URL`.

- A Java instance container has `~/.m2/settings.xml` written before tests run.
- A Go instance container has `GOPROXY` set in env.
- A Rust instance container has `~/.cargo/config.toml` written.
- An instance for a language without a registry variable defined runs with
  Common + Python settings only — no warning, no failure.

### Case 4 — missing required variable

Command: `--corp` is on but `CORP_CA_BUNDLE_PATH` is unset in `.env`.

- Harness exits before any image pull with:
  ```
  corp mode is on but the following are missing or invalid:
    - CORP_CA_BUNDLE_PATH (unset)
  ```
- Exit code is non-zero.
- No partial `results/runs/<id>/` directory is left behind.

### Case 5 — CA bundle path that points at nothing

`CORP_CA_BUNDLE_PATH=/tmp/typo.pem` and that file doesn't exist.

- Harness exits with `CORP_CA_BUNDLE_PATH (file not found: /tmp/typo.pem)`.

### Case 6 — environment-only validation, no Docker

`scripts/run_eval.py --tier lite --offline --corp` (no Docker pull).

- Validation still runs and rejects missing required values.
- Step 1 agent subprocess gets the corporate env.

---

## Open questions

- **Q1 — Flag name**: `--corp` (short, paired with CORP_* prefix), `--internal`,
  or `--corporate-network`? Recommend `--corp`.
- **Q2 — Single `.env` or split `.env.corp`?** Recommend single `.env`,
  with a clearly delimited "Corporate" section in `.env.example`. Reduces
  cognitive load; users who don't need corp mode see commented examples.
- **Q3 — Warn when a language-specific var is set on a tier that doesn't use it?**
  Recommend silent ignore. Users share one `.env` across tiers.
- **Q4 — Java truststore (PEM → JKS).** Most Java SWE-bench instances bundle
  their own deps in the image and don't pull at test time, so PEM-only support
  is likely sufficient. Defer JKS conversion until a real Java instance
  proves it's needed; track as a follow-up.
- **Q5 — Pre-flight connectivity check.** Should the harness curl
  `PIP_INDEX_URL` and `NPM_CONFIG_REGISTRY` from the host before running, to
  catch typos? Recommend yes for the variables that are required (R5) —
  fails-faster on Day 1 configuration mistakes.
- **Q6 — Should the dashboard surface `corp_mode`?** Recommend yes — small
  badge on the run header. Out of scope for the first PR; add when the
  feature lands.
