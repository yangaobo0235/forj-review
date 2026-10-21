"""Verify published business routes against the real PostgreSQL startup path."""

import pytest
from fastapi.testclient import TestClient

from forj_review_service.bootstrap.application import create_app


@pytest.fixture
def published_client(database, monkeypatch):
    from tests.support.postgres import seed_complete_pages

    seed_complete_pages(database)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    with database.connect() as connection:
        original = connection.execute(
            "SELECT release_id, revision FROM cfg_head WHERE id=1"
        ).fetchone()
    with TestClient(create_app()) as client:
        yield client
    with database.connect() as connection:
        assert (
            connection.execute("SELECT release_id, revision FROM cfg_head WHERE id=1").fetchone()
            == original
        )


def test_startup_publishes_all_builtin_business_routes(published_client):
    catalog = published_client.get("/api/review/page-catalog").json()
    assert {(p["business_type"], p["region"]) for p in catalog["identities"]} == {
        ("scrap_replacement", "qingdao"),
        ("scrap_replacement", "changchun"),
        ("vehicle_source", "default"),
        ("transfer", "default"),
        ("consistency", "default"),
    }
    assert len(published_client.get("/api/review/configuration/loaded").json()["pages"]) == 5


@pytest.mark.parametrize("region", ["qingdao", "changchun"])
def test_consistency_page_cannot_submit_as_scrap(published_client, region):
    for route in ("assist", "jobs", "jobs/urls"):
        response = published_client.post(
            f"/api/review/{route}",
            json={
                "page_url": f"https://admin.forjtruck.com/consistency-{region}",
                "business_type": "scrap_replacement",
                "region": region,
                "images": [],
            },
        )
        assert response.status_code == 422, response.text
        assert "不一致" in response.json()["detail"]


@pytest.mark.parametrize(
    "business,region",
    [
        ("scrap_replacement", "qingdao"),
        ("scrap_replacement", "changchun"),
        ("vehicle_source", "default"),
        ("transfer", "default"),
        ("consistency", "default"),
    ],
)
def test_registered_business_requests_use_their_existing_configuration(
    published_client, business, region
):
    manifest = published_client.get(
        "/api/review/collect-manifest",
        params={
            "business_type": business,
            "region": region,
            "profile_version": "1.0",
        },
    )
    if business == "consistency":
        assert manifest.status_code == 404
        assert manifest.json()["detail"] == "该业务尚未声明采集清单"
    else:
        assert manifest.status_code == 200
    for route in ("assist", "jobs", "jobs/urls"):
        response = published_client.post(
            f"/api/review/{route}",
            json={
                "page_url": "https://example.test/review",
                "business_type": business,
                "region": region,
                "images": [],
            },
        )
        assert response.status_code == (200 if route == "assist" else 202), response.text
        if business == "consistency" and route == "assist":
            assert response.json()["recommendation"] == "REVIEW_REQUIRED"
            assert "一致性审核规则尚未配置" in response.json()["summary"]
        if route != "assist":
            assert published_client.app.state.review_jobs.wait(response.json()["job_id"], timeout=5)
