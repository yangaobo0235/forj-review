"""报废车辆排放标准：明确材料值优先，完整型号先查表、未命中再按系列推导。"""

from unittest.mock import Mock

import pytest

from forj_review_service.businesses.profiles import SCRAP_REPLACEMENT_QINGDAO
from forj_review_service.businesses.rules.emission_standard import (
    EMISSION_STANDARD_CHECK_ID,
    run_emission_standard,
)
from forj_review_service.capabilities.specs import ReviewExecutionContext
from forj_review_service.contracts.recognition import RecognitionBatchResult
from forj_review_service.contracts.review import FieldObservation, ReviewRequest
from forj_review_service.integrations.engine_emissions import (
    EngineEmissionLookupUnavailable,
    lookup_engine_emissions,
)


@pytest.fixture(autouse=True)
def lookup(monkeypatch):
    rows = {
        "CA6DL1-28E3F": "国三", "ISF3.8S4R154": "国四", "WP10H400E5": "国五",
        "YC6K560-60": "国六", "SC9DK290Q5": "国五", "MC11.44-50": "国五",
        "BF4M2012-16E4": "国四", "CM6D28.38040": "国四", "D25TCIF1": "国六",
        "Q23-132E60": "国六", "CA6DM2-42E5": "国五",
    }
    lookup = Mock(side_effect=lambda models: {key: rows[key] for key in models if key in rows})
    monkeypatch.setattr(
        __name__ + ".lookup_engine_emissions", lookup,
    )
    return lookup


def observation(field: str, value: object, *, index: int = 1, uncertain: bool = False):
    return FieldObservation(
        field=field,
        source_type="image",
        source_id=f"registration-{index}-{field}",
        image_id=f"registration-{index}",
        image_index=index,
        value=value,
        uncertain=uncertain,
        document_type="registration_certificate",
        business_scope="old_vehicle",
    )


def run(page: object, materials: list[FieldObservation]):
    result = run_emission_standard(
        ReviewExecutionContext(
            request=ReviewRequest(
                page_url="https://example.test/scrap-replace-qingdao/review/1",
                region="qingdao",
                page_fields={"old_vehicle.emission_standard": page},
            ),
            profile=SCRAP_REPLACEMENT_QINGDAO,
            batch=RecognitionBatchResult(),
            observations=tuple(materials),
        ),
        lookup=lookup_engine_emissions,
    )
    assert len(result.checks) == 1
    assert result.checks[0].check_id == EMISSION_STANDARD_CHECK_ID
    return result.checks[0]


def test_registration_certificate_value_is_authoritative() -> None:
    check = run(
        "国Ⅵ",
        [observation("old_vehicle.emission_standard", "国六"), observation("old_vehicle.engine_model", "CA6DL1-28E3F")],
    )

    assert check.status == "MATCH"
    assert check.details["source"] == "registration_certificate"


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("CA6DL1-28E3F", "国三"),
        ("ISF3.8s4R154", "国四"),
        ("WP10H400E5", "国五"),
        ("YC6K560-60", "国六"),
        ("SC9DK290Q5", "国五"),
        ("MC11.44-50", "国五"),
        ("BF4M2012-16E4", "国四"),
        ("CM6D28.380 40", "国四"),
        ("D25TCIF1", "国六"),
        ("Q23-132E60", "国六"),
        ("玉柴 ＹＣ６Ｋ５６０－６０", "国六"),
    ],
)
def test_exact_model_lookup_does_not_require_engine_manufacturer(model: str, expected: str) -> None:
    check = run(
        expected,
        [observation("old_vehicle.engine_model", model)],
    )

    assert check.status == "MATCH"
    assert check.details["source"] == "engine_model"
    assert check.details["standard"] == expected
    assert check.details["lookup_table"] == "engine_emission_standards"


def test_lng_engine_uses_the_same_explicit_registration_standard() -> None:
    check = run(
        "国五",
        [
            observation("old_vehicle.fuel_type", "液化天然气"),
            observation("old_vehicle.emission_standard", "国五"),
            observation("old_vehicle.engine_model", "WP10NG336E5"),
        ],
    )

    assert check.status == "MATCH"


def test_missing_page_value_exposes_registration_standard_as_fill_candidate() -> None:
    check = run("未填写", [observation("old_vehicle.emission_standard", "国五")])

    assert check.status == "INSUFFICIENT"
    assert check.details["standard"] == "国五"
    assert check.details["permitted_fill_values"] == ["国五"]
    candidate = next(item for item in check.values if item.value == "国五" and item.derived_from)
    assert candidate.fill_role == "RECOMMENDATION"


def test_conflicting_page_value_exposes_registration_standard_recommendation() -> None:
    check = run("国五", [observation("old_vehicle.emission_standard", "国四")])

    assert check.status == "CONFLICT"
    candidate = next(item for item in check.values if item.value == "国四" and item.derived_from)
    assert candidate.fill_role == "RECOMMENDATION"


def test_missing_page_value_exposes_engine_model_standard_as_fill_candidate() -> None:
    check = run(
        "",
        [
            observation("old_vehicle.engine_model", "ISF3.8s4R154"),
        ],
    )

    assert check.status == "INSUFFICIENT"
    assert check.details["standard"] == "国四"
    assert check.details["permitted_fill_values"] == ["国四"]
    candidate = next(item for item in check.values if item.value == "国四" and item.derived_from == "ISF3.8S4R154")
    assert candidate.fill_role == "RECOMMENDATION"


def test_unknown_engine_model_requires_manual_review() -> None:
    check = run(
        "国五",
        [observation("old_vehicle.engine_model", "某品牌X500")],
    )

    assert check.status == "INSUFFICIENT"
    assert "未收录完整型号" in check.reason


def test_conflicting_model_markers_are_not_resolved_by_majority() -> None:
    check = run(
        "国五",
        [
            observation("old_vehicle.engine_model", "CA6DL1-28E3F", index=1),
            observation("old_vehicle.engine_model", "CA6DM2-42E5", index=2),
        ],
    )

    assert check.status == "CONFLICT"
    assert "不一致" in check.reason


@pytest.mark.parametrize("model", ["CA4DF3", "WP2.3NQ130E61", "D25TCIE1"])
def test_unlisted_models_without_a_supported_marker_require_manual_review(model):
    check = run("国五", [observation("old_vehicle.engine_model", model)])
    assert check.status == "INSUFFICIENT"
    assert check.details["missing_models"] == [model]
    assert not any(value.fill_role == "RECOMMENDATION" for value in check.values)


@pytest.mark.parametrize(("model", "standard", "rule_id", "marker"), [
    ("SC9DK240Q4", "国四", "sc_q", "Q4"),
    ("WP99.100E5", "国五", "wp_e", "E5"),
    ("YC6K560-60A", "国六", "yc_dash", "-60"),
])
def test_lookup_miss_uses_scoped_pattern(model, standard, rule_id, marker, lookup):
    check = run(standard, [observation("old_vehicle.engine_model", model)])
    lookup.assert_called_once_with([model])
    assert check.status == "MATCH"
    assert check.details["method"] == "pattern"
    assert check.details["lookup_results"] == {}
    assert check.details["rule_results"][model] == {
        "standard": standard, "rule_id": rule_id, "marker": marker,
        "family": check.details["rule_results"][model]["family"],
    }


def test_exact_hit_has_priority_and_never_runs_inference(lookup, monkeypatch):
    lookup.side_effect = None
    lookup.return_value = {"SC9DK240Q4": "国五"}
    inference = Mock(side_effect=AssertionError("精确命中不应推导"))
    monkeypatch.setattr(
        "forj_review_service.businesses.rules.emission_standard.infer_engine_emission", inference,
    )
    check = run("国五", [observation("old_vehicle.engine_model", "SC9DK240Q4")])
    assert check.status == "MATCH"
    assert check.details["method"] == "lookup"
    assert check.details["rule_results"] == {}
    inference.assert_not_called()


@pytest.mark.parametrize(("page", "status"), [("国四", "MATCH"), ("国五", "CONFLICT"),
                                             ("", "INSUFFICIENT")])
def test_mixed_lookup_and_inference_preserve_sources(page, status):
    check = run(page, [observation("old_vehicle.engine_model", "ISF3.8S4R154"),
                       observation("old_vehicle.engine_model", "SC9DK240Q4", index=2)])
    assert check.status == status
    assert check.details["standard"] == "国四"
    assert check.details["method"] == "mixed"
    assert check.details["lookup_results"] == {"ISF3.8S4R154": "国四"}
    assert check.details["rule_results"]["SC9DK240Q4"]["standard"] == "国四"
    if page != "国四":
        assert any(item.fill_role == "RECOMMENDATION" and item.value == "国四"
                   and "规则推导" in item.source for item in check.values)
    if not page:
        assert check.details["permitted_fill_values"] == ["国四"]


def test_lookup_and_inference_disagreement_is_conflict():
    check = run("国五", [observation("old_vehicle.engine_model", "WP10H400E5"),
                         observation("old_vehicle.engine_model", "SC9DK240Q4", index=2)])
    assert check.status == "CONFLICT"
    assert check.details["method"] == "mixed"
    assert not any(item.fill_role == "RECOMMENDATION" for item in check.values)


def test_inference_only_missing_page_shows_rule_source_and_candidate():
    check = run("", [observation("old_vehicle.engine_model", "SC9DK240Q4")])
    assert check.status == "INSUFFICIENT"
    assert check.details["permitted_fill_values"] == ["国四"]
    assert any(item.source == "发动机型号规则推导参考值" and item.derived_from == "SC9DK240Q4"
               and item.value == "国四" for item in check.values)


@pytest.mark.parametrize("model", ["BF4M2012-16E4", "CM6D28.38040", "MC11.44-50"])
def test_series_outside_user_allowlist_only_use_exact_lookup(model, lookup):
    lookup.side_effect = None
    lookup.return_value = {}
    check = run("国五", [observation("old_vehicle.engine_model", model)])
    assert check.status == "INSUFFICIENT"
    assert check.details["rule_results"] == {}


@pytest.mark.parametrize("fuel", ["液化天然气", "汽油", "柴油/天然气"])
def test_explicit_non_diesel_fuel_prevents_fallback(fuel):
    check = run("国四", [observation("old_vehicle.engine_model", "SC9DK240Q4"),
                         observation("old_vehicle.fuel_type", fuel)])
    assert check.status == "INSUFFICIENT"
    assert check.details["fuel_scope"] == "not_eligible"


def test_uncertain_fuel_prevents_fallback():
    check = run("国四", [observation("old_vehicle.engine_model", "SC9DK240Q4"),
                         observation("old_vehicle.fuel_type", "柴油", uncertain=True)])
    assert check.status == "INSUFFICIENT"


def test_explicit_diesel_allows_fallback():
    check = run("国四", [observation("old_vehicle.engine_model", "SC9DK240Q4"),
                         observation("old_vehicle.fuel_type", "柴油")])
    assert check.status == "MATCH"
    assert check.details["fuel_scope"] == "diesel"


def test_explicit_non_diesel_still_allows_exact_lookup():
    check = run("国五", [observation("old_vehicle.engine_model", "WP10H400E5"),
                         observation("old_vehicle.fuel_type", "液化天然气")])
    assert check.status == "MATCH"
    assert check.details["method"] == "lookup"


def test_reference_unavailable_requires_review_instead_of_match(lookup):
    lookup.side_effect = EngineEmissionLookupUnavailable("发动机排放对应表读取失败")
    check = run("国五", [observation("old_vehicle.engine_model", "WP10H400E5")])
    assert check.status == "INSUFFICIENT"
    assert "读取失败" in check.reason


def test_partial_lookup_does_not_ignore_an_unknown_material_model():
    check = run("国六", [observation("old_vehicle.engine_model", "D25TCIF1"),
                         observation("old_vehicle.engine_model", "UNKNOWN", index=2)])
    assert check.status == "INSUFFICIENT"
    assert check.details["missing_models"] == ["UNKNOWN"]


def test_uncertain_engine_reading_never_queries_reference(lookup):
    check = run("国六", [observation("old_vehicle.engine_model", "D25TCIF1", uncertain=True)])
    assert check.status == "INSUFFICIENT"
    lookup.assert_not_called()


def test_explicit_material_value_does_not_need_reference_availability(lookup):
    check = run("国五", [observation("old_vehicle.emission_standard", "国五")])
    assert check.status == "MATCH"
    lookup.assert_not_called()


def test_mismatched_page_value_exposes_exact_lookup_value():
    check = run("国四", [observation("old_vehicle.engine_model", "D25TCIF1")])
    assert check.status == "CONFLICT"
    assert check.details["standard"] == "国六"
    assert any(value.fill_role == "RECOMMENDATION" and value.value == "国六"
               and value.derived_from == "D25TCIF1" for value in check.values)
