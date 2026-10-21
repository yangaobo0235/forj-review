"""All native pages execute the same review graph after standalone conversion."""

import pytest

from forj_review_service.businesses.registry import BusinessRegistry
from forj_review_service.runtime.configuration.service import ReplaySuite, replay_suite


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference, status", [(None, "INSUFFICIENT"), ({"Q23-132E60": "国六"}, "MATCH")]
)
async def test_replay_uses_only_fixed_emission_reference_without_database(
    monkeypatch, reference, status
):
    from unittest.mock import AsyncMock

    from forj_review_service.runtime.review_service import ReviewService
    from tests.support.pages import SCRAP_REPLACEMENT_QINGDAO

    live_lookup = AsyncMock(side_effect=AssertionError("offline replay accessed the database"))
    monkeypatch.setattr(ReviewService, "lookup_engine_emissions", live_lookup)
    profile = SCRAP_REPLACEMENT_QINGDAO
    suite = ReplaySuite.model_validate(
        {
            "cases": [
                {
                    "name": "fixed-emission-reference",
                    "request": {
                        "page_id": profile.page_id,
                        "business_type": str(profile.business_type),
                        "region": str(profile.region),
                        "profile_version": profile.version,
                        "page_url": "https://example.test/scrap-replace-qingdao/review/1",
                        "page_fields": {"old_vehicle.emission_standard": "国六"},
                        "images": [
                            {
                                "index": 1,
                                "image_id": "registration",
                                "src": "https://example.test/registration.jpg",
                                "business_scope": "old_vehicle",
                                "document_type_hint": "registration_certificate",
                            }
                        ],
                    },
                    "batch": {
                        "total_count": 1,
                        "completed_count": 1,
                        "observations": [
                            {
                                "field": "old_vehicle.engine_model",
                                "value": "Q23-132E60",
                                "source_type": "image",
                                "source_id": "registration",
                                "image_id": "registration",
                                "image_index": 1,
                                "document_type": "registration_certificate",
                                "business_scope": "old_vehicle",
                            }
                        ],
                    },
                    "engine_emissions": reference,
                    "expect": {
                        "recommendation": "REVIEW_REQUIRED",
                        "checks": {"EMISSION-STANDARD-001": status},
                    },
                }
            ]
        }
    )
    report = await replay_suite(BusinessRegistry((profile,)), suite)
    assert report["ok"], report
    live_lookup.assert_not_awaited()
