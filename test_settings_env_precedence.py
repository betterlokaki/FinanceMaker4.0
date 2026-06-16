"""Tests for runtime environment overrides over YAML settings."""
from __future__ import annotations

from common.settings import create_settings


def test_flat_prefixed_env_overrides_yaml_nested_config(
    monkeypatch,
) -> None:
    monkeypatch.setenv("EOD_REPORT_ENABLED", "false")

    settings = create_settings()

    assert settings.eod_report.enabled is False


def test_nested_env_overrides_yaml_nested_config(
    monkeypatch,
) -> None:
    monkeypatch.setenv("EOD_REPORT__ENABLED", "false")

    settings = create_settings()

    assert settings.eod_report.enabled is False
