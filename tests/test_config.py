"""Tests for configuration loading and validation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import Config


def test_get_setting_nested(monkeypatch):
    cfg = Config()
    cfg.settings = {"a": {"b": {"c": 42}}}
    assert cfg.get_setting("a", "b", "c") == 42
    assert cfg.get_setting("a", "missing", default="x") == "x"
    assert cfg.get_setting("a", "b", "c", "too-deep", default=None) is None


def test_validate_flags_missing_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    cfg = Config()
    assert any("GEMINI_API_KEY" in p for p in cfg.validate())


def test_validate_flags_placeholder_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "your_gemini_api_key_here")
    cfg = Config()
    assert any("placeholder" in p for p in cfg.validate())


def test_validate_passes_with_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "real-looking-key")
    cfg = Config()
    assert cfg.validate() == []


def test_empty_settings_dont_crash(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    cfg = Config()
    cfg.settings = {}  # simulates empty/missing settings.yaml
    assert cfg.get_setting("gemini", "temperature", default=0.4) == 0.4
