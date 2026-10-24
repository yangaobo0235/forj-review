"""Run the actual exported pages without any database or external calls."""

import hashlib
import importlib.util
import json

import pytest
from fastapi.testclient import TestClient

from forj_review_service.bootstrap.application import create_app
from forj_review_service.businesses.page_config.inspection import profile_fingerprint
from forj_review_service.config.data import DEFAULT_DATA_DIRECTORY, pages_directory
from forj_review_service.contracts.engine_emissions import EngineEmissionLookupUnavailable
from forj_review_service.runtime.configuration.service import validated_configuration


def test_packaged_data_matches_live_database_export():
    source = json.loads((DEFAULT_DATA_DIRECTORY / "source.json").read_text(encoding="utf-8"))
    assert source["source"] == "database-read-only" and source["schema"] == "local"
    for name, checksum in source["sha256"].items():
        assert hashlib.sha256((DEFAULT_DATA_DIRECTORY / name).read_bytes()).hexdigest() == checksum
    pages, _ = validated_configuration(pages_directory())
    assert {p.page_id: p.version for p in pages.profiles} == source["active"]
    assert {p.page_id: profile_fingerprint(p) for p in pages.profiles} == source[
        "effective_fingerprints"
    ]
    emissions = json.loads(
        (DEFAULT_DATA_DIRECTORY / "engine-emissions.json").read_text(encoding="utf-8")
    )
    assert len(emissions) == source["engine_emission_count"]


def test_default_app_serves_all_actual_business_pages_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Default audit runtime must not require external connections")

    monkeypatch.setattr("httpx.AsyncClient.request", forbidden)
    with TestClient(create_app()) as client:
        assert client.get("/health").json() == {"status": "ok"}
        loaded = client.get("/api/review/configuration/loaded").json()
        assert loaded["configuration_source"] == "json"
        for route in (
            "/api/review/configuration/schema",
            "/api/review/configuration/rules",
            "/api/configuration/tasks",
        ):
            assert client.get(route).status_code == 404
        active = client.app.state.review_service.registry.active_profiles
        assert len(active) == 5
        for profile in active:
            if profile.business_type != "consistency":
                manifest = client.get(
                    "/api/review/collect-manifest",
                    params={
                        "business_type": profile.business_type,
                        "page_id": profile.page_id,
                        "region": profile.region,
                        "profile_version": profile.version,
                    },
                )
                assert manifest.status_code == 200
                assert manifest.json()["effective_digest"] == profile_fingerprint(profile)
            result = client.post(
                "/api/review/assist",
                json={
                    "page_url": "https://example.test/review",
                    "business_type": profile.business_type,
                    "region": profile.region,
                    "page_id": profile.page_id,
                    "profile_version": profile.version,
                    "images": [],
                },
            )
            assert result.status_code == 200, result.text
            assert result.json()["recommendation"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize(
    "module", ["psycopg", "sqlalchemy", "forj_review_database", "forj_review_config_service"]
)
def test_runtime_environment_has_no_database_or_administration_package(module):
    assert importlib.util.find_spec(module) is None


def test_missing_pages_fail_startup_instead_of_loading_fixtures(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_PAGES_DIR", str(tmp_path / "absent"))
    with pytest.raises(ValueError):
        with TestClient(create_app()):
            pass


def test_corrupt_reference_fails_startup_instead_of_using_series_rules(tmp_path, monkeypatch):
    path = tmp_path / "reference.json"
    path.write_text('{"MODEL":"invalid"}', encoding="utf-8")
    monkeypatch.setenv("REVIEW_ENGINE_EMISSIONS_FILE", str(path))
    with pytest.raises(EngineEmissionLookupUnavailable):
        with TestClient(create_app()):
            pass
