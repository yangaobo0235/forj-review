"""Business API contracts without a business server or copied catalog database."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from forj_review_service.bootstrap.application import create_app
from forj_review_service.contracts.errors import VehicleModelCatalogUnavailable
from forj_review_service.contracts.vehicle_models import VehicleModelSearch
from forj_review_service.integrations.vehicle_api import VehicleApi, VehicleApiSettings
from forj_review_service.integrations.vehicle_models import VehicleModelCatalog
from forj_review_service.interfaces.http.vehicle_models import model_catalog
from forj_review_service.runtime.review_service import ReviewService
from tests.support.pages import business_registry

SCOPE = {
    "brandId": "2",
    "brandName": "示例品牌",
    "seriesId": "3",
    "seriesName": "示例车系",
    "typeLevel": "牵引车",
}


def api_for(handler):
    return VehicleApi(
        VehicleApiSettings("https://vehicle.test/api/admin"), transport=httpx.MockTransport(handler)
    )


def candidate(identifier="10"):
    return {"modelId": identifier, "modelName": "同名销售车型"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "codes",
    [
        {},
        {"announcementModel": " model100a80 "},
        {"engineModel": "示例 ＥＮＧＩＮＥ４２－Ｅ６"},
        {"announcementModel": "MODEL100A80", "engineModel": "ENGINE42-E6"},
    ],
)
async def test_search_maps_verified_scope_and_optional_codes_without_pagination(codes):
    def handler(request):
        assert request.url.path == "/api/admin/vehicle-model/review/search"
        body = json.loads(request.content)
        assert body == {
            "brandId": "2",
            "serialId": "3",
            "typeLevel": "牵引车",
            "announcementModel": "MODEL100A80" if codes.get("announcementModel") else "",
            "engineType": "ENGINE42-E6" if codes.get("engineModel") else "",
        }
        assert "authorization" not in request.headers
        # All variants, including identical names and more than a page, survive.
        return httpx.Response(
            200, json={"code": 0, "data": [candidate(str(i)) for i in range(1, 102)]}
        )

    result = await VehicleModelCatalog(api_for(handler)).search(
        VehicleModelSearch(pageScope=SCOPE, **codes)
    )
    assert len(result.candidates) == 101
    assert set(result.model_dump()) == {"candidates"}
    assert set(result.candidates[0].model_dump()) == {"modelId", "modelName"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("brandId", ""),
        ("seriesId", ""),
        ("typeLevel", " "),
        ("seriesId", "9223372036854775808"),
        ("seriesId", "-1"),
    ],
)
def test_scope_never_becomes_optional(field, value):
    with pytest.raises(ValidationError):
        VehicleModelSearch(pageScope={**SCOPE, field: value})


@pytest.mark.parametrize(
    "extra",
    [
        {"seriesId": "4"},
        {"page": 2},
        {"pageSize": 5},
        {"announcementModel": "MODEL%"},
        {"engineModel": "ENGINE_42"},
    ],
)
def test_rejects_retired_filters_pagination_and_partial_codes(extra):
    with pytest.raises(ValidationError):
        VehicleModelSearch(pageScope=SCOPE, **extra)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"code": 0},
        {"code": 1, "message": "secret"},
        {"code": 0, "data": [candidate(), candidate()]},
        {"code": 0, "data": [{**candidate(), "modelName": ""}]},
        {"code": 0, "data": [{**candidate(), "modelName": "  "}]},
        {"code": 0, "data": [{**candidate(), "modelId": "0"}]},
        {"code": "0", "data": []},
    ],
)
async def test_bad_model_response_is_failure_not_empty_success(body):
    with pytest.raises(VehicleModelCatalogUnavailable) as error:
        await VehicleModelCatalog(api_for(lambda _: httpx.Response(200, json=body))).search(
            VehicleModelSearch(pageScope=SCOPE)
        )
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_minimal_candidates_and_empty_results_are_normal():
    for rows in [[], [candidate()]]:
        result = await VehicleModelCatalog(
            api_for(lambda _, rows=rows: httpx.Response(200, json={"code": 0, "data": rows}))
        ).search(VehicleModelSearch(pageScope=SCOPE))
        assert len(result.candidates) == len(rows)


@pytest.mark.asyncio
async def test_unused_upstream_specs_are_not_projected_to_the_extension():
    row = {**candidate(), "config": {"maxHp": None}}
    result = await VehicleModelCatalog(
        api_for(lambda _: httpx.Response(200, json={"code": 0, "data": [row]}))
    ).search(VehicleModelSearch(pageScope=SCOPE))
    assert result.model_dump() == {"candidates": [candidate()]}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 500, 302])
async def test_transport_failure_has_safe_message_and_no_database_fallback(status):
    with pytest.raises(VehicleModelCatalogUnavailable) as error:
        await VehicleModelCatalog(api_for(lambda _: httpx.Response(status, text="secret"))).search(
            VehicleModelSearch(pageScope=SCOPE)
        )
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_timeout_invalid_json_and_unconfigured_service_are_explicit_failures():
    def timeout(request):
        raise httpx.ReadTimeout("secret", request=request)

    for api in [
        api_for(timeout),
        api_for(lambda _: httpx.Response(200, text="not-json")),
        VehicleApi(VehicleApiSettings()),
    ]:
        with pytest.raises(VehicleModelCatalogUnavailable):
            await VehicleModelCatalog(api).search(VehicleModelSearch(pageScope=SCOPE))


def test_review_endpoint_proxies_minimal_result_and_exposes_no_series_routes():
    app = create_app(review_service=ReviewService(load_model=False, registry=business_registry()))
    app.dependency_overrides[model_catalog] = lambda: VehicleModelCatalog(
        api_for(lambda _: httpx.Response(200, json={"code": 0, "data": [candidate()]}))
    )
    with TestClient(app) as client:
        response = client.post("/api/review/vehicle-models/search", json={"pageScope": SCOPE})
        assert response.status_code == 200
        assert response.json() == {"candidates": [candidate()]}
        assert client.get("/api/review/vehicle-models/series?brandId=2").status_code == 404
        assert client.get("/api/review/vehicle-models/brands").status_code == 404
        assert client.post("/api/review/vehicle-models/search", json={}).status_code == 422
        app.dependency_overrides[model_catalog] = lambda: VehicleModelCatalog(
            VehicleApi(VehicleApiSettings())
        )
        response = client.post("/api/review/vehicle-models/search", json={"pageScope": SCOPE})
        assert response.status_code == 503
        assert response.json()["code"] == "VEHICLE_CATALOG_UNAVAILABLE"
