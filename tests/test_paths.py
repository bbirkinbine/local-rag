"""Tests for local_rag.paths — canonical filesystem paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_rag.paths import config_path_from_env_or_default, default_config_path


def test_default_config_path_resolves_under_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_config_path() == (tmp_path / ".config" / "local-rag" / "config.toml").resolve()


def test_default_config_path_is_absolute(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_config_path().is_absolute()


def test_env_override_takes_precedence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "custom.toml"
    monkeypatch.setenv("LOCAL_RAG_CONFIG", str(target))

    assert config_path_from_env_or_default() == target.resolve()


def test_env_override_expands_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOCAL_RAG_CONFIG", "~/elsewhere/cfg.toml")

    assert config_path_from_env_or_default() == (tmp_path / "elsewhere" / "cfg.toml").resolve()


def test_empty_env_var_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOCAL_RAG_CONFIG", "")

    assert config_path_from_env_or_default() == default_config_path()


def test_unset_env_var_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LOCAL_RAG_CONFIG", raising=False)

    assert config_path_from_env_or_default() == default_config_path()
