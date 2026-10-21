from dataclasses import replace

import pytest

from forj_review_service.businesses.context import BusinessContext
from forj_review_service.businesses.packs.model import FieldDeclaration
from forj_review_service.contracts.review import FieldObservation, FieldStatus
from forj_review_service.evidence.comparison.aggregate import aggregate_field
from forj_review_service.evidence.fields.differences import value_differences
from forj_review_service.evidence.fields.normalize import normalize_value
from tests.support.pages import SCRAP_BUSINESS as TEST_BUSINESS
from tests.support.pages import SCRAP_REPLACEMENT_QINGDAO


@pytest.fixture
def engine_model_policy():
    profile = replace(
        SCRAP_REPLACEMENT_QINGDAO,
        pack_declaration=replace(
            SCRAP_REPLACEMENT_QINGDAO.pack_declaration,
            fields=(
                FieldDeclaration(
                    "sample.engine_model", "发动机型号", normalizer="engine_model", mode="PARALLEL"
                ),
            ),
        ),
    )
    business = BusinessContext.from_profile(profile)
    return business


def test_detailed_tractor_type_matches_page_business_category() -> None:
    assert normalize_value("old_vehicle.type", "重型半挂牵引车", business=TEST_BUSINESS) == "牵引车"
    assert normalize_value("old_vehicle.type", "牵引车", business=TEST_BUSINESS) == "牵引车"


def test_detailed_cargo_type_matches_page_business_category() -> None:
    assert (
        normalize_value("old_vehicle.type", "中型仓栅式货车", business=TEST_BUSINESS) == "载货汽车"
    )
    assert normalize_value("old_vehicle.type", "载货汽车", business=TEST_BUSINESS) == "载货汽车"


def test_engine_model_compares_only_the_model_itself(engine_model_policy) -> None:
    """证件写“潍柴WP10.5H430E62”，页面可能只写型号；品牌不参与比较。

    品牌和型号是两件事，页面与材料各写各的；带不带品牌都算同一型号。
    """
    assert (
        normalize_value("sample.engine_model", "潍柴WP10.5H430E62", business=engine_model_policy)
        == "WP10.5H430E62"
    )
    assert (
        normalize_value("sample.engine_model", "潍柴 WP10.5H430E62", business=engine_model_policy)
        == "WP10.5H430E62"
    )
    assert normalize_value(
        "sample.engine_model", "潍柴WP10.5H430E62", business=engine_model_policy
    ) == normalize_value("sample.engine_model", "WP10.5H430E62", business=engine_model_policy)
    assert normalize_value(
        "sample.engine_model", "潍柴WP10.5H430E62", business=engine_model_policy
    ) != normalize_value("sample.engine_model", "WP13NG480E61", business=engine_model_policy)


def test_engine_model_comparison_ignores_the_brand_prefix(engine_model_policy) -> None:
    result = aggregate_field(
        "sample.engine_model",
        [
            FieldObservation(
                field="sample.engine_model",
                source_type="page",
                source_id="review_page",
                value="潍柴WP13NG480E61",
            ),
            FieldObservation(
                field="sample.engine_model",
                source_type="image",
                source_id="img-1",
                value="WP13NG480E61",
            ),
        ],
        business=engine_model_policy,
    )
    assert result.status is FieldStatus.MATCH


def test_engine_model_highlight_stays_on_the_model_not_the_brand(engine_model_policy) -> None:
    """品牌不参与比较，高亮也不能标出「缺少 潍柴」。"""
    assert (
        value_differences(
            "sample.engine_model", "潍柴WP13NG480E61", "WP13NG480E61", business=engine_model_policy
        )
        == []
    )
    branded = value_differences(
        "sample.engine_model", "潍柴WP10.5H430E62", "WP13NG480E61", business=engine_model_policy
    )
    plain = value_differences(
        "sample.engine_model", "WP10.5H430E62", "WP13NG480E61", business=engine_model_policy
    )
    assert branded
    assert all(("潍柴" not in item.page_text for item in branded))
    assert [(item.kind, item.start, item.end, item.page_text) for item in branded] == [
        (item.kind, item.start, item.end, item.page_text) for item in plain
    ]
    assert [item.page_start for item in branded] == [item.page_start + 2 for item in plain]
    assert [item.page_end for item in branded] == [item.page_end + 2 for item in plain]


def test_engine_model_preserves_leading_i_and_one_difference() -> None:
    assert (
        normalize_value("old_vehicle.engine_model", "1SGe4-460", business=TEST_BUSINESS)
        == "1SGE4460"
    )
    assert (
        normalize_value("old_vehicle.engine_model", "ISGe4-460", business=TEST_BUSINESS)
        == "ISGE4460"
    )
    result = aggregate_field(
        "old_vehicle.engine_model",
        [
            FieldObservation(
                field="old_vehicle.engine_model",
                source_type="image",
                source_id="img-1",
                value="1SGe4-460",
            ),
            FieldObservation(
                field="old_vehicle.engine_model",
                source_type="page",
                source_id="review_page",
                value="ISGe4-460",
            ),
        ],
        business=TEST_BUSINESS,
    )
    assert result.status is FieldStatus.CONFLICT


def test_fuel_type_treats_electric_labels_as_new_energy() -> None:
    assert normalize_value("new_vehicle.fuel_type", "电", business=TEST_BUSINESS) == "新能源"
    assert normalize_value("new_vehicle.fuel_type", "纯电动", business=TEST_BUSINESS) == "新能源"
    assert normalize_value("new_vehicle.fuel_type", "新能源", business=TEST_BUSINESS) == "新能源"


def test_fuel_type_keeps_hybrid_distinct_from_new_energy() -> None:
    assert (
        normalize_value("new_vehicle.fuel_type", "油电混合", business=TEST_BUSINESS) == "混合动力"
    )
    assert (
        normalize_value("new_vehicle.fuel_type", "混合动力", business=TEST_BUSINESS) == "混合动力"
    )
    assert normalize_value(
        "new_vehicle.fuel_type", "电", business=TEST_BUSINESS
    ) != normalize_value("new_vehicle.fuel_type", "混合动力", business=TEST_BUSINESS)


def test_old_fuel_type_keeps_lng_and_cng_distinct() -> None:
    assert (
        normalize_value("old_vehicle.fuel_type", "液化天然气", business=TEST_BUSINESS)
        == "液化天然气（LNG）"
    )
    assert (
        normalize_value("old_vehicle.fuel_type", "压缩天然气", business=TEST_BUSINESS)
        == "压缩天然气（CNG）"
    )
    assert normalize_value(
        "old_vehicle.fuel_type", "液化天然气", business=TEST_BUSINESS
    ) != normalize_value("old_vehicle.fuel_type", "压缩天然气", business=TEST_BUSINESS)


def test_jiefang_flag_normalizes_registration_brand() -> None:
    assert normalize_value("old_vehicle.is_jiefang", "一汽解放", business=TEST_BUSINESS) == "是"
    assert normalize_value("old_vehicle.is_jiefang", "欧曼", business=TEST_BUSINESS) == "否"
