"""Tests for application configuration."""

import pytest

from app.config import Settings


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings read the API key from the environment and apply defaults.

    Inputs: GEMINI_API_KEY set in the environment.
    Expected: the secret is loaded and accessible, and gemini_model falls back
        to its default.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    settings = Settings()

    assert settings.gemini_api_key.get_secret_value() == "test-key"
    assert settings.gemini_model == "gemini-2.5-flash"
    assert "application/pdf" in settings.allowed_mime_types


def test_missing_api_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing API key raises at construction rather than failing later.

    Inputs: no GEMINI_API_KEY in the environment and no .env file.
    Expected: instantiating Settings raises a validation error.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(Exception):
        Settings(_env_file=None)  # type: ignore[call-arg]