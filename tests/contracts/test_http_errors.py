"""HTTP errors classify public failures without reflecting internal or request data."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forj_review_config_service.contracts.models import ConfigurationError
from forj_review_config_service.interfaces.errors import install_error_handlers as config_errors
from forj_review_service.contracts.errors import ReviewInputError
from forj_review_service.contracts.review import ReviewRequest
from forj_review_service.interfaces.http.errors import install_error_handlers as review_errors


@pytest.mark.parametrize("install", [review_errors, config_errors])
@pytest.mark.parametrize("failure", [ValueError, RuntimeError])
def test_internal_errors_have_stable_response_without_exception_values(install, failure, caplog):
    app = FastAPI()
    install(app)

    @app.get("/fail")
    def fail():
        raise failure("postgresql://account:private-password@db/internal_table")

    with TestClient(app, raise_server_exceptions=False) as client:
        result = client.get("/fail")
    assert result.status_code == 500
    assert result.json()["code"] == "INTERNAL_ERROR"
    assert "private-password" not in result.text
    assert "private-password" not in caplog.text
    assert failure.__name__ in caplog.text
    assert "test_http_errors.py" in caplog.text


@pytest.mark.parametrize("install", [review_errors, config_errors])
def test_request_validation_does_not_echo_input_or_validator_exception(install):
    app = FastAPI()
    install(app)

    @app.post("/validate")
    def validate(request: ReviewRequest):
        return {"ok": True}

    with TestClient(app) as client:
        result = client.post(
            "/validate", json={"page_url": "https://example.test/review", "images": "private-input"}
        )
    assert result.status_code == 422
    assert "private-input" not in result.text
    assert isinstance(result.json()["message"], str)


@pytest.mark.parametrize(
    "install,error,code,status",
    [
        (review_errors, ReviewInputError("图片清单包含重复标识"), "INVALID_REVIEW_INPUT", 422),
        (
            config_errors,
            ConfigurationError("BASELINE_CHANGED", "请重新验证候选"),
            "BASELINE_CHANGED",
            409,
        ),
    ],
)
def test_known_business_errors_retain_actionable_messages(install, error, code, status):
    app = FastAPI()
    install(app)

    @app.get("/fail")
    def fail():
        raise error

    with TestClient(app) as client:
        result = client.get("/fail")
    assert result.status_code == status
    assert result.json()["code"] == code
    assert result.json()["message"] == str(error)


@pytest.mark.parametrize("route", ["assist", "jobs", "jobs/urls"])
def test_review_routes_do_not_classify_internal_value_errors_as_bad_input(route):
    from forj_review_service.interfaces.http.dependencies import get_review_jobs, get_review_service
    from forj_review_service.interfaces.http.review import router

    class BrokenService:
        calls = 0

        def resolve_profile(self, request):
            self.calls += 1
            raise ValueError("private-storage-details")

    service = BrokenService()
    app = FastAPI()
    review_errors(app)
    app.include_router(router)
    app.dependency_overrides[get_review_service] = lambda: service
    app.dependency_overrides[get_review_jobs] = lambda: object()
    with TestClient(app, raise_server_exceptions=False) as client:
        result = client.post(
            f"/api/review/{route}", json={"page_url": "https://example.test/review", "images": []}
        )
    assert service.calls == 1
    assert result.status_code == 500
    assert result.json()["code"] == "INTERNAL_ERROR"
    assert "private-storage-details" not in result.text
