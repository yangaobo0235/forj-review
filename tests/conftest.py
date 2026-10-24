"""Tests use deterministic fixtures and never consume private service credentials."""

import os
import tempfile

import pytest

os.environ.setdefault("REVIEW_LOG_DIR", tempfile.mkdtemp(prefix="review-service-test-logs-"))


@pytest.fixture(autouse=True)
def isolate_private_service_settings(monkeypatch, tmp_path):
    from forj_review_service.config import settings

    monkeypatch.setattr(settings, "DEFAULT_ENV_FILE", tmp_path / "no-private-settings.env")
    monkeypatch.setenv("REVIEW_VEHICLE_API_BASE_URL", "")
    monkeypatch.setenv("REVIEW_VEHICLE_API_TOKEN", "")
    monkeypatch.setenv("REVIEW_VEHICLE_API_TIMEOUT", "15")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
