"""Tests for src/core/corp_env.py."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.core.corp_env import (
    CONTAINER_CA_PATH,
    CorpConfig,
    CorpConfigError,
    build_container_env,
    build_docker_run_args,
    build_host_env,
    load,
    metadata_dict,
    raise_on_invalid,
    validate,
)


# ---------------------------------------------------------------------------
# Disabled mode (default — no --corp flag)
# ---------------------------------------------------------------------------

class TestDisabledMode:
    def test_load_disabled_returns_empty_config(self, monkeypatch):
        # Even with env vars set, disabled should produce an empty config
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:8080")
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", "/etc/ssl/ca.pem")
        cfg = load(enabled=False)
        assert cfg.enabled is False
        assert cfg.proxy == {}
        assert cfg.ca_bundle_host_path is None

    def test_validate_disabled_never_errors(self):
        assert validate(CorpConfig(enabled=False)) == []
        # raise_on_invalid is a no-op for disabled configs
        raise_on_invalid(CorpConfig(enabled=False))

    def test_build_host_env_disabled_returns_empty(self):
        assert build_host_env(CorpConfig(enabled=False)) == {}

    def test_build_container_env_disabled_returns_empty(self):
        assert build_container_env(CorpConfig(enabled=False)) == {}

    def test_docker_run_args_disabled_returns_empty(self):
        assert build_docker_run_args(CorpConfig(enabled=False)) == []

    def test_metadata_disabled(self):
        assert metadata_dict(CorpConfig(enabled=False)) == {"corp_mode": False}


# ---------------------------------------------------------------------------
# Enabled mode: load()
# ---------------------------------------------------------------------------

class TestLoad:
    def test_load_picks_up_proxy_and_ca(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"
        ca.write_text("dummy cert")
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:8080")
        monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp:8080")
        monkeypatch.setenv("NO_PROXY", ".corp,.local")
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", str(ca))
        monkeypatch.setenv("PIP_INDEX_URL", "https://mirror.corp/pypi/simple/")

        cfg = load(enabled=True)
        assert cfg.enabled
        assert cfg.proxy["HTTPS_PROXY"] == "http://proxy.corp:8080"
        assert cfg.proxy["HTTP_PROXY"] == "http://proxy.corp:8080"
        assert cfg.proxy["NO_PROXY"] == ".corp,.local"
        assert cfg.ca_bundle_host_path == ca
        assert cfg.python["PIP_INDEX_URL"] == "https://mirror.corp/pypi/simple/"

    def test_load_multi_lang_vars(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"; ca.write_text("x")
        monkeypatch.setenv("HTTPS_PROXY", "http://p:8080")
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", str(ca))
        monkeypatch.setenv("PIP_INDEX_URL", "https://m/pypi/")
        monkeypatch.setenv("NPM_CONFIG_REGISTRY", "https://m/npm/")
        monkeypatch.setenv("GOPROXY", "https://m/go,direct")
        monkeypatch.setenv("CORP_MAVEN_MIRROR_URL", "https://m/maven/")
        monkeypatch.setenv("CORP_CARGO_REGISTRY_URL", "sparse+https://m/crates/")
        monkeypatch.setenv("CORP_GEM_SOURCE", "https://m/rubygems/")
        monkeypatch.setenv("CORP_COMPOSER_REPO_URL", "https://m/composer/")
        monkeypatch.setenv("CORP_APT_MIRROR_URL", "http://m/ubuntu")

        cfg = load(enabled=True, tier="multi")
        assert cfg.js["NPM_CONFIG_REGISTRY"] == "https://m/npm/"
        assert cfg.go["GOPROXY"] == "https://m/go,direct"
        assert cfg.maven_mirror_url == "https://m/maven/"
        assert cfg.cargo_registry_url == "sparse+https://m/crates/"
        assert cfg.gem_source == "https://m/rubygems/"
        assert cfg.composer_repo_url == "https://m/composer/"
        assert cfg.apt_mirror_url == "http://m/ubuntu"

    def test_load_missing_optionals_is_ok(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"; ca.write_text("x")
        monkeypatch.setenv("HTTPS_PROXY", "http://p")
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", str(ca))
        monkeypatch.setenv("PIP_INDEX_URL", "https://m/pypi/")
        # Don't set any optionals
        cfg = load(enabled=True)
        assert cfg.go == {}
        assert cfg.js == {}
        assert cfg.maven_mirror_url == ""


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------

class TestValidate:
    def test_validate_ok(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"; ca.write_text("x")
        monkeypatch.setenv("HTTPS_PROXY", "http://p")
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", str(ca))
        monkeypatch.setenv("PIP_INDEX_URL", "https://m/pypi/")
        cfg = load(enabled=True)
        assert validate(cfg) == []

    def test_validate_missing_https_proxy(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"; ca.write_text("x")
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", str(ca))
        monkeypatch.setenv("PIP_INDEX_URL", "https://m/pypi/")
        cfg = load(enabled=True)
        errs = validate(cfg)
        assert any("HTTPS_PROXY" in e for e in errs)

    def test_validate_missing_ca_path(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://p")
        monkeypatch.delenv("CORP_CA_BUNDLE_PATH", raising=False)
        monkeypatch.setenv("PIP_INDEX_URL", "https://m/pypi/")
        cfg = load(enabled=True)
        errs = validate(cfg)
        assert any("CORP_CA_BUNDLE_PATH" in e for e in errs)

    def test_validate_ca_path_not_a_file(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://p")
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", "/tmp/nonexistent-typo.pem")
        monkeypatch.setenv("PIP_INDEX_URL", "https://m/pypi/")
        cfg = load(enabled=True)
        errs = validate(cfg)
        assert any("not found" in e for e in errs)

    def test_validate_missing_pip_index(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"; ca.write_text("x")
        monkeypatch.setenv("HTTPS_PROXY", "http://p")
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", str(ca))
        monkeypatch.delenv("PIP_INDEX_URL", raising=False)
        cfg = load(enabled=True)
        errs = validate(cfg)
        assert any("PIP_INDEX_URL" in e for e in errs)

    def test_raise_on_invalid_raises(self, monkeypatch):
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("CORP_CA_BUNDLE_PATH", raising=False)
        monkeypatch.delenv("PIP_INDEX_URL", raising=False)
        cfg = load(enabled=True)
        with pytest.raises(CorpConfigError) as exc:
            raise_on_invalid(cfg)
        msg = str(exc.value)
        assert "HTTPS_PROXY" in msg
        assert "CORP_CA_BUNDLE_PATH" in msg
        assert "PIP_INDEX_URL" in msg


# ---------------------------------------------------------------------------
# build_host_env()
# ---------------------------------------------------------------------------

class TestBuildHostEnv:
    def test_fans_out_ca_to_all_tools(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"; ca.write_text("x")
        monkeypatch.setenv("HTTPS_PROXY", "http://p")
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", str(ca))
        monkeypatch.setenv("PIP_INDEX_URL", "https://m/pypi/")
        cfg = load(enabled=True)
        env = build_host_env(cfg)
        # CA fan-out covers all tool-native env vars
        assert env["REQUESTS_CA_BUNDLE"] == str(ca)
        assert env["SSL_CERT_FILE"] == str(ca)
        assert env["CURL_CA_BUNDLE"] == str(ca)
        assert env["GIT_SSL_CAINFO"] == str(ca)
        assert env["NODE_EXTRA_CA_CERTS"] == str(ca)
        assert env["BUNDLE_SSL_CA_CERT"] == str(ca)
        assert env["CARGO_HTTP_CAINFO"] == str(ca)

    def test_translates_gem_source(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"; ca.write_text("x")
        monkeypatch.setenv("HTTPS_PROXY", "http://p")
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", str(ca))
        monkeypatch.setenv("PIP_INDEX_URL", "https://m/pypi/")
        monkeypatch.setenv("CORP_GEM_SOURCE", "https://m/rubygems/")
        cfg = load(enabled=True)
        env = build_host_env(cfg)
        assert env.get("BUNDLE_MIRROR__HTTPS://RUBYGEMS__ORG") == "https://m/rubygems/"


# ---------------------------------------------------------------------------
# build_container_env()
# ---------------------------------------------------------------------------

class TestBuildContainerEnv:
    def test_ca_paths_point_at_mount(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"; ca.write_text("x")
        monkeypatch.setenv("HTTPS_PROXY", "http://p")
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", str(ca))
        monkeypatch.setenv("PIP_INDEX_URL", "https://m/pypi/")
        cfg = load(enabled=True)
        env = build_container_env(cfg)
        # In-container CA paths point at the mount path, NOT the host path
        assert env["REQUESTS_CA_BUNDLE"] == CONTAINER_CA_PATH
        assert env["NODE_EXTRA_CA_CERTS"] == CONTAINER_CA_PATH


# ---------------------------------------------------------------------------
# build_docker_run_args()
# ---------------------------------------------------------------------------

class TestDockerRunArgs:
    def test_emits_env_and_volume_flags(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"; ca.write_text("x")
        monkeypatch.setenv("HTTPS_PROXY", "http://p:8080")
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", str(ca))
        monkeypatch.setenv("PIP_INDEX_URL", "https://m/pypi/")
        cfg = load(enabled=True)
        args = build_docker_run_args(cfg)
        # -e flags
        assert "-e" in args
        assert "HTTPS_PROXY=http://p:8080" in args
        assert "PIP_INDEX_URL=https://m/pypi/" in args
        # -v flag with mount path
        assert "-v" in args
        assert any(f"{ca}:{CONTAINER_CA_PATH}:ro" in a for a in args)


# ---------------------------------------------------------------------------
# metadata_dict()
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_metadata_records_active_groups(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"; ca.write_text("x")
        monkeypatch.setenv("HTTPS_PROXY", "http://p")
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", str(ca))
        monkeypatch.setenv("PIP_INDEX_URL", "https://m/pypi/")
        monkeypatch.setenv("CORP_MAVEN_MIRROR_URL", "https://m/maven/")
        cfg = load(enabled=True)
        meta = metadata_dict(cfg)
        assert meta["corp_mode"] is True
        groups = meta["corp_active_groups"]
        assert groups["proxy"] is True
        assert groups["ca_bundle"] is True
        assert groups["python_mirror"] is True
        assert groups["maven"] is True
        assert groups["go"] is False  # Not set
        assert groups["js"] is False

    def test_metadata_does_not_leak_values(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"; ca.write_text("x")
        monkeypatch.setenv("HTTPS_PROXY", "http://secret-proxy:8080")
        monkeypatch.setenv("CORP_CA_BUNDLE_PATH", str(ca))
        monkeypatch.setenv("PIP_INDEX_URL", "https://secret-mirror.corp/pypi/")
        cfg = load(enabled=True)
        meta = metadata_dict(cfg)
        # Serialized metadata must not contain the actual URL/path values
        as_json = str(meta)
        assert "secret-proxy" not in as_json
        assert "secret-mirror" not in as_json
        assert str(ca) not in as_json
