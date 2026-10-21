"""Configuration sessions requirements."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from forj_review_config_service.bootstrap.application import create_app
from tests.support.configuration import (
    SNAPSHOT,
    FakeModel,
    call,
    create,
    headers,
    wait,
)


@pytest.mark.parametrize(
    "snapshot",
    [
        {
            **SNAPSHOT,
            "fields": [
                {"candidate_id": "same", "label": "A"},
                {"candidate_id": "same", "label": "B"},
            ],
        },
        {**SNAPSHOT, "samples": [{"sample_id": "same", "url": "https://example.test/a"}] * 2},
        {**SNAPSHOT, "page_url": "javascript:alert(1)"},
        {**SNAPSHOT, "samples": [{"sample_id": "a", "url": "file:///private.png"}]},
        {**SNAPSHOT, "samples": [{"sample_id": "a", "url": "https://user:secret@example.test/a"}]},
    ],
)
def test_capture_rejects_ambiguous_ids_and_non_http_sources(settings, snapshot):
    with TestClient(create_app(settings, model=FakeModel())) as client:
        response = client.post(
            "/api/review-config/captures",
            json={
                "client_request_id": "invalid-capture",
                "extension_id": "a" * 32,
                "snapshot": snapshot,
            },
        )
        assert response.status_code == 422
        assert client.portal.call(client.app.state.service.store.list_captures) == []


def test_cancel_serializes_with_following_session_commands(settings):
    from forj_review_config_service.contracts.models import MessageCommand

    with TestClient(create_app(settings, model=FakeModel())) as client:
        sid = create(client, headers())
        service = client.app.state.service

        async def scenario():
            stopping, release = (asyncio.Event(), asyncio.Event())

            async def worker():
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    stopping.set()
                    await release.wait()
                    raise

            service.tasks[sid] = asyncio.create_task(worker())
            await asyncio.sleep(0)
            cancelling = asyncio.create_task(service.cancel(sid))
            await stopping.wait()
            following = asyncio.create_task(
                service.command(
                    sid,
                    "message",
                    MessageCommand(
                        client_request_id="after-cancel", expected_revision=0, text="重新核对"
                    ),
                )
            )
            try:
                await asyncio.sleep(0)
                assert not following.done(), "a new turn must wait until cancellation finishes"
            finally:
                release.set()
                await cancelling
                await following

        client.portal.call(scenario)


def test_local_access_without_pairing_and_creation_idempotency(settings):
    with TestClient(create_app(settings, model=FakeModel())) as client:
        assert client.get("/health").json()["service"] == "review-config-service"
        assert client.get("/api/review-config/sessions").status_code == 200
        assert not (settings.state_dir / "access.json").exists()
        auth = headers()
        assert (
            client.get(
                "/api/review-config/sessions", headers={**auth, "origin": "https://evil.test"}
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/review-config/sessions", headers={**auth, "host": "evil.test"}
            ).status_code
            == 403
        )
        for origin in (
            "chrome-extension://" + "b" * 32,
            "http://localhost:5173",
            "http://127.0.0.1:26102",
        ):
            response = client.get("/api/review-config/sessions", headers={"origin": origin})
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin
            preflight = client.options(
                "/api/review-config/sessions",
                headers={
                    "origin": origin,
                    "access-control-request-method": "POST",
                    "access-control-request-headers": "content-type",
                },
            )
            assert preflight.status_code == 200
            assert preflight.headers["access-control-allow-headers"] == "content-type"
            assert preflight.headers["vary"] == "Origin"
        for origin in (
            "null",
            "http://localhost.evil.test",
            "chrome-extension://invalid",
            "http://127.0.0.1:26102@evil.test",
        ):
            assert (
                client.options(
                    "/api/review-config/sessions", headers={"origin": origin}
                ).status_code
                == 403
            )
        first = create(client, auth)
        assert create(client, auth) == first
        response = client.post(
            "/api/review-config/sessions",
            headers=auth,
            json={"client_request_id": "create-1", "snapshot": {**SNAPSHOT, "title": "changed"}},
        )
        assert response.status_code == 409
        assert not hasattr(client.app.state, "review_jobs")


def test_only_human_can_refresh_scope_and_old_browser_task_becomes_stale(settings):
    with TestClient(
        create_app(settings, model=FakeModel([call("request_collection_trial", call_id="browser")]))
    ) as client:
        auth = headers()
        sid = create(client, auth)
        client.post(
            f"/api/review-config/sessions/{sid}/messages",
            headers=auth,
            json={"client_request_id": "message", "expected_revision": 0, "text": "核对配置"},
        )
        wait(client, sid, auth, "WAITING")
        response = client.post(
            f"/api/review-config/sessions/{sid}/page",
            headers=auth,
            json={
                "client_request_id": "refresh",
                "expected_revision": 0,
                "snapshot": {**SNAPSHOT, "snapshot_id": "new"},
            },
        )
        assert response.status_code == 200
        record = client.portal.call(client.app.state.service.store.get, sid)
        assert record.revision == 1 and record.pending is None and (record.report is None)
        assert record.snapshot.snapshot_id == "new"


def test_capture_handoff_is_durable_idempotent_and_bound_to_session(settings):
    command = {"client_request_id": "capture1", "extension_id": "a" * 32, "snapshot": SNAPSHOT}
    with TestClient(create_app(settings, model=FakeModel())) as client:
        response = client.post("/api/review-config/captures", headers=headers(), json=command)
        assert response.status_code == 200
        capture_id = response.json()["capture_id"]
        assert (
            client.post("/api/review-config/captures", json=command).json()["capture_id"]
            == capture_id
        )
        assert (
            client.post(
                "/api/review-config/captures", json={**command, "extension_id": "b" * 32}
            ).status_code
            == 409
        )
        assert client.get("/api/review-config/captures").json()[0]["field_count"] == 0
    with TestClient(create_app(settings, model=FakeModel())) as client:
        capture = client.get(f"/api/review-config/captures/{capture_id}").json()
        assert (
            capture["extension_id"] == "a" * 32 and capture["snapshot"]["snapshot_id"] == "snapshot"
        )
        payload = {
            "client_request_id": "web-session",
            "snapshot": SNAPSHOT,
            "capture_id": capture_id,
            "page_id": "example_permit",
        }
        mismatch = {**payload, "snapshot": {**SNAPSHOT, "snapshot_id": "different"}}
        assert (
            client.post("/api/review-config/sessions", json=mismatch).json()["code"]
            == "CAPTURE_MISMATCH"
        )
        response = client.post("/api/review-config/sessions", json=payload)
        assert response.status_code == 200
        sid = response.json()["session_id"]
        assert client.get(f"/api/review-config/sessions/{sid}").json()["capture_id"] == capture_id
        assert client.delete(f"/api/review-config/captures/{capture_id}").status_code == 409
        new_capture = client.post(
            "/api/review-config/captures",
            json={
                **command,
                "client_request_id": "capture2",
                "snapshot": {**SNAPSHOT, "snapshot_id": "snapshot2"},
            },
        ).json()["capture_id"]
        refresh = {
            "client_request_id": "refresh",
            "expected_revision": 0,
            "capture_id": new_capture,
            "snapshot": {**SNAPSHOT, "snapshot_id": "snapshot2"},
        }
        assert (
            client.post(f"/api/review-config/sessions/{sid}/page", json=refresh).status_code == 200
        )
        assert client.get(f"/api/review-config/sessions/{sid}").json()["capture_id"] == new_capture
        assert client.delete(f"/api/review-config/captures/{capture_id}").status_code == 200
        assert client.get(f"/api/review-config/captures/{capture_id}").status_code == 404
